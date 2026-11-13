-- Source-bound still-capture provider proposal. No native calls until constructed.
return function(sdk, env, options)
    if options==nil then options={} end
    assert(type(options)=='table' and getmetatable(options)==nil,'capture constructor options')
    local profile_name=options.capacity_profile
    if profile_name==nil then profile_name='interactive' end
    assert(profile_name=='interactive' or profile_name=='campaign_v1','capacity profile')
    local caps
    if options.capacity_resolver==nil then
        assert(profile_name=='interactive','campaign requires source-bound capacity resolver')
        caps={name='interactive',capture_history=512,capture_calls=2048,capture_native_records=512}
    else
        assert(type(options.capacity_resolver)=='function','capacity resolver')
        caps=options.capacity_resolver(profile_name)
    end
    assert(type(caps)=='table' and getmetatable(caps)==nil and caps.name==profile_name,'capacity identity')
    for _,k in ipairs({'capture_history','capture_calls','capture_native_records'})do
        assert(type(caps[k])=='number' and caps[k]>=1 and caps[k]<=16384 and caps[k]%1==0,'capacity bound')
    end
    local max_records,max_calls,max_native_records=caps.capture_history,caps.capture_calls,caps.capture_native_records
    local capacity_revision,capacity_source=caps.revision,caps.source_sha256
    if options.capacity_resolver==nil then
        capacity_revision,capacity_source='UNBOUND_LEGACY_INTERACTIVE','UNBOUND_LEGACY_INTERACTIVE'
    else
        for _,v in ipairs({capacity_revision or false,capacity_source or false})do
            assert(type(v)=='string' and #v==64 and not v:find('[^0-9a-f]'),'capacity source identity')
        end
    end
    local ID, REV, MD = "x4-screen-capture-v1", "@REVISION@", "@MD_REVISION@"
    local api, records, pending, seen = {}, {}, {}, {}
    local busy, stamp, fault, last, serial, count = nil,nil,nil,nil,0,0
    function api.capacity()
        return {profile=profile_name,revision=capacity_revision,source_sha256=capacity_source,
            history_used=count,history_limit=max_records,
            calls_used=serial,calls_limit=max_calls,scope='LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS'}
    end
    for _,k in ipairs({"send","subscribe","read","clear","now","public_current","component_id","dispose"}) do
        assert(type(env[k])=="function","missing capture channel dependency: "..k)
    end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function finite(v) return type(v)=="number" and v==v and math.abs(v)<math.huge end
    local function fields(t, wanted, n)
        assert(type(t)=="table" and getmetatable(t)==nil,"plain object required")
        local size=0;for k in pairs(t) do assert(wanted[k],"unexpected field");size=size+1 end
        assert(size==n,"missing field")
    end
    local function now()
        local v=env.now()
        assert(finite(v) and v>=0 and (last==nil or v>=last),"invalid/backward clock")
        last=v;return v
    end
    local function current()
        assert(not fault,"capture channel quarantined")
        local v=env.public_current()
        fields(v,{provider=true,revision=true,session=true,generation=true,world_token=true},5)
        assert(v.provider=="x4-live-session-v1" and hex(v.revision,64) and hex(v.session,32)
            and hex(v.generation,32) and type(v.world_token)=="string" and #v.world_token>0
            and #v.world_token<=4096,"public identity unavailable")
        if stamp then for k,x in pairs(stamp) do assert(v[k]==x,"public identity changed") end
        else stamp={};for k,x in pairs(v) do stamp[k]=x end end
        now();return stamp
    end
    local function fail(why)
        fault=fault or why
        local old=pending;pending={}
        for _,p in pairs(old) do
            pcall(p.done,{status="unknown",reason=fault,observations={
                adapter_revision=REV,file_completion_qualified=false,visual_pass=false}})
        end
    end
    local function copy(v,key,depth,budget)
        budget.n=budget.n+1;assert(depth<=5 and budget.n<=300,"observation structure bound")
        if (key=="prepared_target" or key=="capture_target") and v~=nil then
            local ok,id=pcall(env.component_id,v)
            if ok and type(id)=="string" and id:match("^[1-9]%d*ULL$") then
                local digits=id:sub(1,-4)
                if #digits<20 or (#digits==20 and digits<="18446744073709551615") then
                    return {status="OBSERVED",id=id,issued_for_query=false}
                end
            end
            return {status="UNKNOWN",reason="NATIVE_COMPONENT_UNREADABLE"}
        end
        local kind=type(v)
        if kind=="nil" or kind=="boolean" then return v end
        if kind=="number" then assert(finite(v),"unknown numeric observation");return v end
        if kind=="string" then
            budget.bytes=budget.bytes+#v;assert(#v<=4096 and budget.bytes<=30000,"observation byte bound");return v
        end
        assert(kind=="table" and getmetatable(v)==nil,"unreadable observation")
        local result={}
        for k,x in pairs(v) do assert(type(k)=="string" and #k<=128,"observation key");result[k]=copy(x,k,depth+1,budget) end
        return result
    end
    local function duplicate(v)
        if type(v)~="table" then return v end
        local out={};for k,x in pairs(v) do out[k]=duplicate(x) end;return out
    end
    local function local_result(r,status,reason)
        return {status=status,reason=reason,observations={adapter_revision=REV,native_revision=MD,
            capture_id=r.capture and r.capture.id or r.id,file_completion_qualified=false,
            visual_pass=false,reservation_released=r.released==true,capacity=api.capacity()}}
    end
    local function mutation_pending()
        for _,p in pairs(pending) do if p.phase~="status" then return true end end
        return false
    end
    local function status_pending(r)
        for _,p in pairs(pending) do if p.phase=="status" and p.record==r then return true end end
        return false
    end
    local function tick()
        current();local age=now()
        for _,p in pairs(pending) do
            if age-p.age>10 then fail("CAPTURE_REPLY_TIMEOUT_QUARANTINED_NO_REPLAY");return end
        end
    end
    -- MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0 (vanilla
    -- ego_detailmonitorhelper/helper.lua:8824 tests a blackboard flag with ~= 0). Exactly 1/0
    -- of the NAMED reply fields become booleans; any other value is left for the checks to refuse.
    local function md_flags(row,names)
        for _,k in ipairs(names) do
            if row[k]==1 then row[k]=true elseif row[k]==0 then row[k]=false end
        end
    end
    local function receipt(row,preflight)
        fields(row,{version=true,id=true,session=true,digest=true,status=true,observations=true,reason=true},7)
        assert(row.version==1 and hex(row.id,32) and hex(row.session,32) and hex(row.digest,64)
            and type(row.reason)=="string" and #row.reason<=4096,"receipt identity")
        local o=row.observations
        assert(type(o)=="table" and type(o.bridge_call)=="string","transport correlation missing")
        md_flags(o,{'file_confirmed','visual_pass','reservation_released','md_busy'})
        local p=pending[o.bridge_call]
        if not p then assert(not seen[o.bridge_call],"duplicate delivered capture reply");return end
        current()
        assert(row.id==p.native.id and row.digest==p.native.digest and row.session==stamp.session
            and o.bridge_phase==p.phase and o.bridge_generation==stamp.generation
            and o.revision==MD,"capture receipt rebound")
        if p.phase=='capacity'then
            fields(o,{bridge_phase=true,bridge_call=true,bridge_generation=true,revision=true,age=true,
                world_token=true,public_revision=true,md_used=true,md_limit=true,md_busy=true,md_queued=true,
                md_queue_limit=true,capacity_profile=true,capacity_revision=true},14)
            assert(row.status=='observed' and row.reason=='READ_ONLY_CAPACITY_SAMPLE'
                and o.world_token==stamp.world_token and o.public_revision==stamp.revision,'capacity world/status')
            assert(o.capacity_profile==profile_name and o.capacity_revision==capacity_revision,'native capacity identity')
            assert(o.md_limit==max_native_records and finite(o.md_used) and o.md_used%1==0
                and o.md_used>=0 and o.md_used<=o.md_limit,'native record count')
            assert(o.md_queue_limit==64 and finite(o.md_queued) and o.md_queued%1==0
                and o.md_queued>=0 and o.md_queued<64 and type(o.md_busy)=='boolean','native queue/busy')
        else
            assert(o.directory=='x4_toolkit_capture' and o.file_confirmed==false and o.visual_pass==false,'capture scope')
            assert(row.status=='prepared' or row.status=='running' or row.status=='refused','unqualified native completion')
        end
        assert(finite(o.age) and o.age>=0,"capture observation age missing")
        local values=copy(o,nil,0,{n=0,bytes=0})
        values.adapter_revision=REV;values.native_revision=MD;values.file_completion_qualified=false
        values.capacity=api.capacity()
        if p.phase=='capacity'then
            values.native_observation_cached=false;values.scope='READ_ONLY_MD_CAPACITY_SAMPLE'
            values.visual_pass=false
        end
        if p.phase=="release" then
            assert(p.record.consumed and o.reservation_released==true
                and row.status==p.record.capture.state
                and row.reason=="RESERVATION_RELEASED_NOT_FILE_CONFIRMATION","reservation release unconfirmed/contradictory")
        end
        current();assert(now()-p.age<=10,'response validation deadline')
        if preflight then return end
        pending[o.bridge_call]=nil;seen[o.bridge_call]=true
        local result={status=row.status,reason=row.reason,observations=values}
        if p.phase=='capacity'then
            result.status='completed';p.record.last_result=duplicate(result)
        elseif p.phase=="release" then
            p.record.released=true;p.record.capture.released=true;busy=nil
            result.status="completed";result.reason="RESERVATION_RELEASED_NOT_FILE_COMPLETION"
        elseif p.record.action=="capture" then
            local old=p.record.last_result
            local stale=p.phase=="status" and ((p.record.consumed and row.status=="prepared")
                or (old and old.observations.age>o.age))
            if not stale then
                p.record.state=row.status;p.record.last_result=result
            end
        end
        p.done(result)
    end
    local function drain()
        if fault or not stamp then return end
        local ok,why=pcall(function()
            tick();assert(not fault,"capture channel quarantined")
            local rows=env.read();if rows==nil then return end
            assert(type(rows)=="table" and getmetatable(rows)==nil and #rows<=64,"reply queue bound")
            for k in pairs(rows) do assert(type(k)=="number" and k>=1 and k%1==0 and k<=#rows,"sparse reply queue") end
            env.clear()
            local calls={}
            for _,row in ipairs(rows) do
                assert(type(row)=="table" and type(row.observations)=="table","reply observation object")
                local call=row.observations.bridge_call
                assert(type(call)=="string" and not calls[call],"duplicate call in native reply batch")
                calls[call]=true;receipt(row,true)
            end
            -- No callback can dispatch capture before the entire already-read batch qualifies.
            for _,row in ipairs(rows) do assert(not fault,"retired during reply delivery");receipt(row) end
        end)
        if not ok then fail("CAPTURE_PROTOCOL_FAULT: "..tostring(why)) end
    end
    local function send(r,phase,done)
        current()
        local n=0;for _ in pairs(pending) do n=n+1 end
        assert(n<64 and serial<max_calls,"capture transport capacity")
        serial=serial+1;local call=stamp.generation.."."..serial
        local native=r.capture or r
        pending[call]={record=r,native=native,phase=phase,done=done,age=now()}
        local ok,why=pcall(env.send,phase=='capacity' and 'capacity' or 'request',
            {id=native.id,digest=native.digest,session=stamp.session,binding=REV,generation=stamp.generation,
            phase=phase,call=call,world_token=stamp.world_token,public_revision=stamp.revision,md_revision=MD})
        if not ok then fail("CAPTURE_SEND_UNCERTAIN_NO_REPLAY: "..tostring(why)) end
    end
    local function invoke(phase,q,done)
        tick();current()
        assert(q.provider==ID and q.binding==REV and q.session==stamp.session and hex(q.id,32)
            and hex(q.digest,64),"capture request binding")
        assert(phase=="prepare" or phase=="execute" or phase=="status","unknown phase")
        local r=records[q.id]
        if r then assert(r.digest==q.digest and r.action==q.action,"capture request identity changed") end
        if q.action=='capacity_observe'then
            fields(q.arguments,{},0)
            if phase=='prepare'then
                assert(not r and count<max_records and not mutation_pending(),'capacity observation busy/exhausted')
                r={id=q.id,digest=q.digest,action=q.action,consumed=false,prepared=now()}
                records[q.id]=r;count=count+1
                done(local_result(r,'prepared','READ_ONLY_CAPACITY_PREPARED'))
            elseif phase=='execute'then
                assert(r and not r.consumed and now()-r.prepared<=10 and not mutation_pending(),'capacity observation not prepared')
                r.consumed=true;send(r,'capacity',done)
            else
                assert(r,'capacity observation missing')
                if r.last_result then
                    local out=duplicate(r.last_result);out.observations.native_observation_cached=true
                    out.observations.capacity=api.capacity();done(out)
                else done(local_result(r,r.consumed and 'unknown' or 'prepared','NO_CAPACITY_REPLAY'))end
            end
        elseif q.action=="capture" then
            fields(q.arguments,{view_binding=true},1)
            assert(type(q.arguments.view_binding)=="string" and #q.arguments.view_binding>0
                and #q.arguments.view_binding<=128,"bounded view attribution required")
            if phase=="prepare" then
                assert(not r and not busy and count<max_records,"capture reservation unavailable")
                r={id=q.id,digest=q.digest,action=q.action,consumed=false,state="preparing"}
                records[q.id]=r;count=count+1;busy=r
                send(r,"prepare",done)
            elseif phase=="execute" then
                assert(r and busy==r and r.state=="prepared" and not r.consumed,"capture not prepared")
                r.consumed=true;send(r,"execute",done)
            else
                assert(r,"unknown capture")
                -- Coalesce native reads; SDK status itself completes once immediately.
                -- A delayed native status refresh only updates the measured cache.
                if not mutation_pending() and not status_pending(r) then send(r,"status",function()end) end
                local cached=r.last_result
                if cached and ((not r.consumed and cached.status=="prepared")
                    or (r.consumed and cached.status~="prepared")) then
                    local observed=duplicate(cached)
                    observed.observations.capacity=api.capacity()
                    observed.observations.native_observation_cached=true
                    observed.observations.native_status_refresh_pending=status_pending(r)
                    done(observed)
                else done(local_result(r,"unknown","NATIVE_OBSERVATION_PENDING_NO_REPLAY")) end
            end
        elseif q.action=="release" then
            fields(q.arguments,{capture_id=true,capture_digest=true},2)
            assert(hex(q.arguments.capture_id,32) and hex(q.arguments.capture_digest,64),"release target identity")
            local target=records[q.arguments.capture_id]
            assert(target and target.action=="capture" and target.digest==q.arguments.capture_digest,"foreign release target")
            if phase=="prepare" then
                assert(not r and busy==target and target.consumed and not target.release_request
                    and (target.state=="running" or target.state=="refused") and not mutation_pending()
                    and count<max_records,"capture cannot be released")
                r={id=q.id,digest=q.digest,action=q.action,capture=target,consumed=false}
                records[q.id]=r;count=count+1;target.release_request=q.id
                done(local_result(r,"prepared","BOOKKEEPING_ONLY_HOST_SETTLEMENT_REQUIRED"))
            elseif phase=="execute" then
                assert(r and busy==target and not r.consumed,"release already consumed or target changed")
                r.consumed=true;send(r,"release",done)
            else
                assert(r,"unknown release")
                done(local_result(r,r.released and "completed" or (r.consumed and "unknown" or "prepared"),
                    r.released and "RESERVATION_RELEASED_NOT_FILE_COMPLETION" or "NO_RELEASE_REPLAY"))
            end
        else error("unknown capture action") end
    end
    local retired,retire_failed=false,false
    function api.dispose()
        if retire_failed then return false end
        if retired then return true end
        retired=true;fail("CAPTURE_ADAPTER_DISPOSED")
        local ok,result=pcall(env.dispose)
        if not ok or result==false then retire_failed=true;return false end
        return true
    end
    function api.state()
        local ok,why=pcall(tick)
        if not ok and stamp then fail(tostring(why)) end
        return {ready=ok and stamp~=nil and not fault,fault=fault,reservation=busy and busy.id or false,
            file_completion_qualified=false,visual_pass=false}
    end
    local ok,why=pcall(function()
        -- Extensions attach before the public world handshake. Only a validated
        -- public_current observation may establish the first binding later.
        env.subscribe(drain)
        sdk.register({id=ID,revision=REV,actions={capture=true,release=true,capacity_observe=true},dispose=api.dispose,
            session=function()
                local good,value=pcall(function()tick();return current()end)
                if not good then
                    if stamp then fail(tostring(value)) end
                    return nil
                end
                return value.session
            end,invoke=invoke})
    end)
    if not ok then
        local clean=api.dispose()
        error("capture constructor failed: "..tostring(why)..(clean and "" or "; retirement failed"))
    end
    return api
end

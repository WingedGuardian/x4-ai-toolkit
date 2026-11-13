-- Diagnostic named-room transport. Completion means observed room equality only.
return function(d)
    assert(type(d)=='table' and d.ffi and type(d.channel)=='table','roomentry dependencies')
    local c,ffi=d.channel,d.ffi
    for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
        assert(type(c[k])=='function','channel method '..k)
    end
    for _,k in ipairs({'resolve_issued','encode'})do assert(type(d[k])=='function','dependency '..k)end
    local profile=d.capacity_profile
    if profile==nil then profile='interactive' end
    assert(profile=='interactive' or profile=='campaign_v1','capacity profile')
    local caps
    if d.capacity_resolver==nil then
        assert(profile=='interactive','campaign requires source-bound capacity resolver')
        caps={name='interactive',roomentry_records=64,roomentry_calls=256}
    else
        assert(type(d.capacity_resolver)=='function','capacity resolver')
        caps=d.capacity_resolver(profile)
    end
    assert(type(caps)=='table' and getmetatable(caps)==nil and caps.name==profile,'capacity identity')
    local max_records,max_calls=caps.roomentry_records,caps.roomentry_calls
    assert(type(max_records)=='number' and max_records%1==0 and max_records>=1 and max_records<=256
        and type(max_calls)=='number' and max_calls%1==0 and max_calls>=1 and max_calls<=8192,'capacity limits')
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    local capacity_revision,capacity_source=caps.revision,caps.source_sha256
    if d.capacity_resolver==nil then
        capacity_revision,capacity_source='UNBOUND_LEGACY_INTERACTIVE','UNBOUND_LEGACY_INTERACTIVE'
    else assert(hex(capacity_revision,64) and hex(capacity_source,64),'capacity source identity')end
    assert(hex(d.revision,64) and hex(d.md_revision,64),'source revisions')
    local api,records,count,serial={}, {},0,0
    local choices={}
    local stamp,last_clock,pending,fault,retired,dispose_failure=nil,nil,nil,nil,false,false
    local fields={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision',
        'status','reason','target','target_exists','room','room_exists','entered','controlled','age','dispatched','consumed','positioned','room_count','candidates',
        'candidate_macros','room_macro','scope_count','scope_complete','choice_required'}
    local usage_fields={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision',
        'status','reason','age','md_used','md_limit','md_busy','md_queued','md_queue_limit','capacity_profile','capacity_revision'}
    local function copy(v)
        if type(v)~='table'then return v end
        local x={};for k,a in pairs(v)do x[k]=copy(a)end;return x
    end
    local function current()
        assert(not retired and not fault,'roomentry retired or faulted')
        local s=c.public_current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=512,'canonical identity')
        local x={}
        for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            if stamp then assert(stamp[k]==s[k],'identity changed')end;x[k]=s[k]
        end
        stamp=x
        local now=c.now()
        assert(type(now)=='number' and now==now and now>=0 and now<math.huge
            and (not last_clock or now>=last_clock),'invalid clock')
        last_clock=now;return x,now
    end
    local function result(status,reason,r)
        local o={diagnostic_transport=true,walking_verified=false,docking_verified=false,
            access_rules_verified=false,room_geometry_verified=false,runtime_verified=false}
        o.capacity={profile=profile,revision=capacity_revision,source_sha256=capacity_source,
            history_used=count,history_limit=max_records,calls_used=serial,calls_limit=max_calls,
            scope='LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS'}
        if r and r.last then
            o.snapshot=copy(r.last);o.native_observation_cached=r.cached==true
            if r.last.kind=='ROOM_CAPACITY'then o.diagnostic_transport=false;o.scope='READ_ONLY_MD_CAPACITY_SAMPLE'end
        end
        local value={status=status,reason=reason,observations=o}
        assert(#d.encode({payload=d.encode(value),reserved=string.rep('x',900)})<=4000,'reply bound')
        return value
    end
    local function fail(reason)
        fault=tostring(reason)
        local p=pending;pending=nil
        if p then p.r.uncertain=true;p.done(result('unknown','transport unavailable; no retry',nil))end
    end
    local function target(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued container token')
        local id=d.resolve_issued(token,'object')
        assert(type(id)=='cdata' and ffi.istype('uint64_t',id) and id~=0,'exact issued native ID')
        local text=c.component_id(id)
        assert(type(text)=='string' and text==tostring(id),'native target conversion drift')
        return id,text
    end
    local function arguments(req)
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'arguments')
        if req.action=='capacity_observe'then assert(next(a)==nil,'capacity arguments must be empty');return nil,nil,nil end
        local n=0;for k in pairs(a)do assert(k=='container' or k=='selector' or k=='room_choice','closed arguments');n=n+1 end
        assert(n==2 or n==3,'container and selector required')
        assert(type(a.selector)=='string' and #a.selector<=128 and
            (a.selector=='controlroom' or a.selector=='dynamicrooms' or a.selector:match('^[a-z0-9_]+_macro$')),'exact room selector')
        assert(a.room_choice==nil or (type(a.room_choice)=='string' and #a.room_choice<=64),'room choice token')
        return a.container,a.selector,a.room_choice
    end
    local function check(req,r)
        local s,now=current()
        assert(type(req)=='table' and req.provider=='x4-diagnostic-roomentry-v1'
            and (req.action=='diagnostic_enter_room' or req.action=='capacity_observe') and req.binding==d.revision
            and req.session==s.session and hex(req.id,32) and hex(req.digest,64),'request identity')
        local token,selector,choice=arguments(req)
        if r then assert(r.digest==req.digest and r.token==token and r.session==req.session
            and r.action==req.action and r.selector==selector and r.choice_token==choice,'request changed')end
        return s,now,token,selector,choice
    end
    local function validate(row,p)
        assert(type(row)=='table' and getmetatable(row)==nil,'reply row')
        local selected=p.phase=='capacity' and usage_fields or fields
        local expected={};for _,k in ipairs(selected)do expected[k]=true end
        local n=0;for k in pairs(row)do assert(expected[k],'unknown reply field');n=n+1 end
        assert(n==#selected,'missing reply field')
        assert(row.kind==(p.phase=='capacity' and 'ROOM_CAPACITY' or 'ROOM_ENTRY') and row.revision==d.md_revision and row.id==p.r.id
            and row.digest==p.r.digest and row.call==p.call and row.phase==p.phase,'reply correlation')
        for _,k in ipairs({'session','generation','world_token'})do assert(row[k]==stamp[k],'reply world')end
        assert(row.public_revision==stamp.revision,'reply public revision')
        assert(type(row.reason)=='string' and #row.reason<=256,'reply reason')
        assert(type(row.age)=='number' and row.age==row.age and row.age>=0 and row.age<math.huge,'native age')
        if p.r.last then assert(row.age>=p.r.last.age,'native age rollback')end
        if p.phase=='capacity'then
            assert(row.status=='observed' and row.reason=='READ_ONLY_CAPACITY_SAMPLE','capacity status')
            assert(row.capacity_profile==profile and row.capacity_revision==capacity_revision,'native capacity identity')
            assert(row.md_limit==max_records and type(row.md_used)=='number' and row.md_used%1==0
                and row.md_used>=0 and row.md_used<=max_records,'native record count')
            assert(row.md_queue_limit==64 and type(row.md_queued)=='number' and row.md_queued%1==0
                and row.md_queued>=0 and row.md_queued<64 and type(row.md_busy)=='boolean','native queue/busy')
            return copy(row)
        end
        assert(row.status=='prepared' or row.status=='refused' or row.status=='dispatched','reply status')
        for _,k in ipairs({'target_exists','room_exists','entered','controlled','dispatched','consumed','positioned','scope_complete','choice_required'})do
            assert(type(row[k])=='boolean','reply boolean')
        end
        assert(row.target_exists,'target unavailable')
        assert(c.component_id(row.target)==p.r.target_text,'target echo differs')
        assert(not row.dispatched or row.consumed,'dispatch before consumption')
        if row.status=='prepared'then assert(not row.consumed and not row.dispatched,'contradictory preparation')end
        if row.status=='dispatched'then assert(row.dispatched and row.consumed,'missing dispatch evidence')end
        if p.phase=='prepare'then assert(not row.dispatched and not row.consumed,'prepare side effect')end
        if p.r.dispatched then assert(row.dispatched,'dispatch state lost')end
        assert(not row.positioned or row.dispatched,'position before dispatch')
        assert(not row.entered or (row.room_exists and not row.controlled),'contradictory room observation')
        assert(type(row.scope_count)=='number' and row.scope_count>=0 and row.scope_count%1==0 and row.scope_count<=1000000,'native context denominator')
        if row.scope_complete then
            assert(type(row.room_count)=='number' and row.room_count>=0 and row.room_count%1==0 and row.room_count<=1000000,'room denominator')
        else assert(row.room_count==false and row.status=='refused','incomplete native scope cannot prepare or dispatch')end
        assert(row.choice_required==(p.r.selector=='dynamicrooms' and p.r.choice_token==nil),'choice requirement differs')
        assert(not row.choice_required or row.status=='refused','preview cannot auto-select')
        assert(type(row.candidates)=='table' and getmetatable(row.candidates)==nil,'room candidates')
        local count=0;for k in pairs(row.candidates)do
            assert(type(k)=='number' and k%1==0 and k>=1 and k<=16,'candidate index');count=count+1
        end
        assert(count==#row.candidates and count<=16,'candidate bound')
        assert(count==(row.scope_complete and row.room_count<=16 and row.room_count or 0),'candidate denominator differs')
        if p.phase=='prepare' and p.r.selector=='dynamicrooms' and row.scope_complete and row.room_count>0 then
            assert(row.scope_count>0,'rooms without observed native context')
        end
        if p.phase~='prepare'then assert(count==0 and (row.room_count==0 or row.room_count==false),'unexpected room enumeration')end
        assert(type(row.candidate_macros)=='table' and getmetatable(row.candidate_macros)==nil,'candidate macro list')
        local macrocount=0;for k in pairs(row.candidate_macros)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=16,'macro index');macrocount=macrocount+1 end
        assert(macrocount==count and #row.candidate_macros==count,'macro denominator differs')
        local function macro(v)return type(v)=='string' and #v<=128 and v:match('^[a-z0-9_]+_macro$')end
        assert((not row.room_exists and row.room_macro=='') or (row.room_exists and macro(row.room_macro)),'room macro unavailable')
        local out=copy(row);out.target=p.r.target_text;out.candidates={};out.candidate_macros=nil;out.macro_ids={}
        local seen,macros,observed={},{},{}
        for i,object in ipairs(row.candidates)do
            local id=c.component_id(object);assert(type(id)=='string' and not seen[id],'duplicate room identity');seen[id]=true
            local model=row.candidate_macros[i];assert(macro(model),'candidate macro unavailable');observed[id]=model
            if not macros[model]then out.macro_ids[#out.macro_ids+1]=model;macros[model]=#out.macro_ids end
            local token=p.r.id..':'..tostring(i)
            choices[token]={object=object,id=id,target=p.r.target_text,selector=p.r.selector,macro=model}
            out.candidates[i]={choice=token,identity=id,macro_index=macros[model]}
        end
        out.room=row.room_exists and c.component_id(row.room) or ''
        if row.status=='prepared' and p.phase=='prepare'then
            assert(row.room_exists and seen[out.room],'prepared room not in observed candidates')
            assert(observed[out.room]==row.room_macro,'prepared room macro differs')
            if p.r.choice_object then assert(out.room==p.r.choice_id,'prepared room differs from choice')end
            if p.r.choice_macro then assert(row.room_macro==p.r.choice_macro,'chosen macro changed')end
        end
        if p.r.room_id then assert(row.room_exists and out.room==p.r.room_id and row.room_macro==p.r.room_macro,'retained room changed')end
        out.room_query_observed=p.phase=='prepare'
        if not out.room_query_observed then
            out.room_count=nil;out.scope_count=nil;out.scope_complete=nil
        end
        return out
    end
    local function drain()
        if retired or fault or not stamp then return end
        local ok,reason=pcall(function()
            current();local rows=c.read();c.clear();current()
            assert(type(rows)=='table' and getmetatable(rows)==nil,'reply batch')
            local n=0;for k in pairs(rows)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=64,'batch index');n=n+1 end
            assert(n==#rows and n<=64,'batch shape')
            if n==0 then return end
            -- Single-flight: preflight the complete batch before any callback can send another action.
            assert(n==1 and pending,'unsolicited or duplicate response batch')
            local p=pending;local value=validate(rows[1],p);local _,ended=current()
            assert(ended-p.started<=10,'response validation deadline')
            local status=value.status
            if status=='observed'then status='completed'
            elseif status=='dispatched'then
                status=(value.entered and value.positioned) and 'completed' or 'running'
            end
            -- Prove the complete encoded response before releasing the pending callback.
            -- Failure must still settle it as unknown, never leave a prepared request pending.
            local response=result(status,value.reason,{last=value})
            pending=nil;p.r.last=value;p.r.dispatched=value.dispatched
            if value.status=='prepared'then p.r.room_id=value.room;p.r.room_macro=value.room_macro end
            p.done(response)
        end)
        if not ok then fail(reason)end
    end
    function api.tick()
        if retired or fault then return end
        local ok,reason=pcall(function()
            local _,now=current()
            if pending then assert(now-pending.started<=10,'MD response timeout; no resend')end
        end)
        if not ok and stamp then fail(reason)end
    end
    local function send(phase,req,r,done)
        assert(not pending and not r.uncertain,'unresolved transport')
        local s,now=current();local id,text
        if phase~='capacity'then id,text=target(r.token);assert(text==r.target_text,'issued target drift')end
        current();assert(serial<max_calls,'transport capacity');serial=serial+1
        local call=string.format('%032x',serial)
        local body={revision=d.md_revision,id=req.id,digest=req.digest,call=call,
            session=s.session,generation=s.generation,world_token=s.world_token,public_revision=s.revision,
            target=id,selector=r.selector,choice=r.choice_object or ffi.new('uint64_t',0)} -- retain native uint64, never a decimal/ULL string
        pending={phase=phase,r=r,done=done,call=call,started=now}
        if phase=='execute' or phase=='capacity'then r.sent=true end -- before potentially throwing native send
        c.send(phase,body)
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback')
        local r=type(req)=='table' and records[req.id] or nil
        local delivered=false
        local function once(value)assert(not delivered,'duplicate local delivery');delivered=true;done(value)end
        local ok,reason=pcall(function()
            local _,now,token,selector,choice=check(req,r)
            if req.action=='capacity_observe'then
                if phase=='prepare'then
                    assert(not r and count<max_records and not pending,'duplicate or busy/capacity')
                    r={id=req.id,digest=req.digest,session=req.session,action=req.action,prepared=now,sent=false}
                    records[req.id]=r;count=count+1
                    return once(result('prepared','READ_ONLY_CAPACITY_PREPARED',r))
                end
                assert(r,'capacity preparation missing')
                if phase=='execute'then
                    assert(not r.sent and now-r.prepared<=10 and not pending,'capacity not prepared/busy')
                    return send('capacity',req,r,once)
                end
                assert(phase=='status','unknown phase')
                if r.last then return once(result('completed','historical capacity sample',{last=r.last,cached=true}))end
                return once(result(r.sent and 'unknown' or 'prepared','NO_CAPACITY_REPLAY',r))
            end
            if phase=='prepare'then
                assert(not r and count<max_records and not pending,'duplicate or busy/capacity')
                local id,text=target(token);current()
                local selected=choice and choices[choice] or nil
                assert(not choice or (selected and selected.target==text and selected.selector==selector),'unobserved or foreign room choice')
                r={id=req.id,digest=req.digest,session=req.session,action=req.action,token=token,target_text=text,prepared=now,sent=false,
                    selector=selector,choice_token=choice,choice_object=selected and selected.object,choice_id=selected and selected.id,
                    choice_macro=selected and selected.macro}
                records[req.id]=r;count=count+1
                return send(phase,req,r,once)
            end
            assert(r,'preparation missing')
            if pending then return once(result('running','another MD reply remains pending',r))end
            if phase=='execute'then
                assert(not r.sent and r.last and r.last.status=='prepared' and now-r.prepared<=10,'not prepared or already sent')
            else assert(phase=='status','unknown phase')end
            send(phase,req,r,once)
        end)
        if not ok then
            if pending and pending.r==r then fail(reason)
            elseif not delivered then once(result('unknown',tostring(reason):sub(1,256),r))end
        end
    end
    function api.dispose()
        if dispose_failure then return false end
        if retired then return true end;retired=true
        if pending then pending.r.uncertain=true;pending=nil end
        local ok,v=pcall(c.dispose)
        if not ok or v~=true then dispose_failure=true;return false end
        return true
    end
    api.spec={id='x4-diagnostic-roomentry-v1',revision=d.revision,
        actions={diagnostic_enter_room=true,capacity_observe=true},session=function()
            api.tick();local ok,s=pcall(current);return ok and s.session or nil
        end,invoke=invoke,dispose=api.dispose}
    local ok,reason=pcall(function()c.arm_tick(api.tick);c.subscribe(drain)end)
    if not ok then api.dispose();error(reason)end
    return api
end

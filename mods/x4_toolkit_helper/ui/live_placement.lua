-- Diagnostic transport provider. Native action return is not arrival or ship-control proof.
return function(d)
    assert(type(d)=='table' and d.ffi and type(d.channel)=='table','placement dependencies')
    local c,ffi=d.channel,d.ffi
    for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
        assert(type(c[k])=='function','channel method '..k)
    end
    for _,k in ipairs({'resolve_issued','encode'})do assert(type(d[k])=='function','dependency '..k)end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    assert(hex(d.revision,64) and hex(d.md_revision,64),'source revisions')
    local api,records,count,serial={}, {},0,0
    local stamp,last_clock,pending,fault,retired,dispose_failure=nil,nil,nil,nil,false,false
    local fields={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision',
        'status','reason','target','target_exists','occupied','controlled','container','age','dispatched','consumed'}
    local function copy(v)
        if type(v)~='table'then return v end
        local x={};for k,a in pairs(v)do x[k]=copy(a)end;return x
    end
    local function current()
        assert(not retired and not fault,'placement retired or faulted')
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
            control_requested_attribute='OMITTED_NATIVE_DEFAULT',runtime_verified=false}
        if r and r.last then o.snapshot=copy(r.last)end
        local value={status=status,reason=reason,observations=o}
        assert(#d.encode({payload=d.encode(value),reserved=string.rep('x',900)})<=4000,'reply bound')
        return value
    end
    local function fail(reason)
        fault=tostring(reason)
        local p=pending;pending=nil
        if p then p.r.uncertain=true;p.done(result('unknown','transport unavailable; no retry',p.r))end
    end
    local function target(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued ship token')
        local id=d.resolve_issued(token,'ship')
        assert(type(id)=='cdata' and ffi.istype('uint64_t',id) and id~=0,'exact issued native ID')
        local text=c.component_id(id)
        assert(type(text)=='string' and text==tostring(id),'native target conversion drift')
        return id,text
    end
    local function arguments(req)
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'arguments')
        local n=0;for k in pairs(a)do assert(k=='ship','closed argument vocabulary');n=n+1 end
        assert(n==1,'one issued ship required');return a.ship
    end
    local function check(req,r)
        local s,now=current()
        assert(type(req)=='table' and req.provider=='x4-diagnostic-placement-v1'
            and req.action=='diagnostic_place_player' and req.binding==d.revision
            and req.session==s.session and hex(req.id,32) and hex(req.digest,64),'request identity')
        local token=arguments(req)
        if r then assert(r.digest==req.digest and r.token==token and r.session==req.session,'request changed')end
        return s,now,token
    end
    local function validate(row,p)
        assert(type(row)=='table' and getmetatable(row)==nil,'reply row')
        local expected={};for _,k in ipairs(fields)do expected[k]=true end
        local n=0;for k in pairs(row)do assert(expected[k],'unknown reply field');n=n+1 end
        assert(n==#fields,'missing reply field')
        assert(row.kind=='PLACEMENT' and row.revision==d.md_revision and row.id==p.r.id
            and row.digest==p.r.digest and row.call==p.call and row.phase==p.phase,'reply correlation')
        for _,k in ipairs({'session','generation','world_token'})do assert(row[k]==stamp[k],'reply world')end
        assert(row.public_revision==stamp.revision,'reply public revision')
        assert(type(row.reason)=='string' and #row.reason<=256,'reply reason')
        assert(row.status=='prepared' or row.status=='refused' or row.status=='dispatched','reply status')
        assert(type(row.age)=='number' and row.age==row.age and row.age>=0 and row.age<math.huge,'native age')
        if p.r.last then assert(row.age>=p.r.last.age,'native age rollback')end
        for _,k in ipairs({'target_exists','occupied','controlled','container','dispatched','consumed'})do
            assert(type(row[k])=='boolean','reply boolean')
        end
        assert(row.target_exists,'target unavailable')
        assert(c.component_id(row.target)==p.r.target_text,'target echo differs')
        assert(not row.dispatched or row.consumed,'dispatch before consumption')
        if row.status=='prepared'then assert(not row.consumed and not row.dispatched,'contradictory preparation')end
        if row.status=='dispatched'then assert(row.dispatched and row.consumed,'missing dispatch evidence')end
        if p.phase=='prepare'then assert(not row.dispatched and not row.consumed,'prepare side effect')end
        if p.r.dispatched then assert(row.dispatched,'dispatch state lost')end
        local out=copy(row);out.target=p.r.target_text
        return out
    end
    local function drain()
        if retired or fault then return end
        local ok,reason=pcall(function()
            current();local rows=c.read();c.clear();current()
            assert(type(rows)=='table' and getmetatable(rows)==nil,'reply batch')
            local n=0;for k in pairs(rows)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=64,'batch index');n=n+1 end
            assert(n==#rows and n<=64,'batch shape')
            if n==0 then return end
            -- Single-flight: preflight the complete batch before any callback can send another action.
            assert(n==1 and pending,'unsolicited or duplicate response batch')
            local p=pending;local value=validate(rows[1],p);current()
            pending=nil;p.r.last=value;p.r.dispatched=value.dispatched
            local status=value.status
            if status=='dispatched'then
                status=(value.occupied or value.controlled) and 'completed' or 'running'
            end
            p.done(result(status,value.reason,p.r))
        end)
        if not ok then fail(reason)end
    end
    function api.tick()
        if retired or fault then return end
        local ok,reason=pcall(function()
            local _,now=current()
            if pending then assert(now-pending.started<=10,'MD response timeout; no resend')end
        end)
        if not ok then fail(reason)end
    end
    local function send(phase,req,r,done)
        assert(not pending and not r.uncertain,'unresolved transport')
        local s,now=current();local id,text=target(r.token)
        assert(text==r.target_text,'issued target drift')
        current();serial=serial+1;assert(serial<=256,'transport capacity')
        local call=string.format('%032x',serial)
        local body={revision=d.md_revision,id=req.id,digest=req.digest,call=call,
            session=s.session,generation=s.generation,world_token=s.world_token,public_revision=s.revision,
            target=id} -- retain native uint64, never a decimal/ULL string
        pending={phase=phase,r=r,done=done,call=call,started=now}
        if phase=='execute'then r.sent=true end -- before potentially throwing native send
        c.send(phase,body)
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback')
        local r=type(req)=='table' and records[req.id] or nil
        local delivered=false
        local function once(value)assert(not delivered,'duplicate local delivery');delivered=true;done(value)end
        local ok,reason=pcall(function()
            local _,now,token=check(req,r)
            if phase=='prepare'then
                assert(not r and count<64 and not pending,'duplicate or busy')
                local id,text=target(token);current()
                r={id=req.id,digest=req.digest,session=req.session,token=token,target_text=text,prepared=now,sent=false}
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
    api.spec={id='x4-diagnostic-placement-v1',revision=d.revision,
        actions={diagnostic_place_player=true},session=function()
            api.tick();local ok,s=pcall(current);return ok and s.session or nil
        end,invoke=invoke,dispose=api.dispose}
    local ok,reason=pcall(function()c.arm_tick(api.tick);c.subscribe(drain)end)
    if not ok then api.dispose();error(reason)end
    return api
end

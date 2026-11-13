-- Fixed diagnostic sector-pose requests. No flight, collision or visual claims.
return function(d)
    assert(type(d)=='table' and d.ffi and type(d.channel)=='table','ship placement dependencies')
    local c,ffi=d.channel,d.ffi
    for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
        assert(type(c[k])=='function','channel method '..k)
    end
    for _,k in ipairs({'resolve_issued','encode','capacity_resolver'})do assert(type(d[k])=='function','dependency '..k)end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local profile=d.capacity_profile;if profile==nil then profile='interactive' end
    assert(profile=='interactive' or profile=='campaign_v1','fixed capacity profile')
    local cap=d.capacity_resolver(profile)
    assert(type(cap)=='table' and cap.name==profile and hex(cap.revision,64) and hex(cap.source_sha256,64)
        and cap.native_history==(profile=='interactive' and 128 or 512),'canonical native history')
    local max_records,max_calls=cap.native_history,cap.native_history*8
    local caprevision,capsource=cap.revision,cap.source_sha256
    assert(hex(d.revision,64) and hex(d.md_revision,64),'source revisions')
    local api,records,count,serial={}, {},0,0
    local stamp,last_clock,pending,fault,retired,dispose_failure=nil,nil,nil,nil,false,false
    local parameters={'x_m','y_m','z_m','yaw_degrees','pitch_degrees','roll_degrees'}
    local common={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision','age','status','reason'}
    local nativefields={'target','target_exists','sector','pose_observed','eligible','pose','desired','speed_mps',
        'position_match','orientation_match','dispatched','consumed','busy'}
    local usagefields={'md_used','md_limit','md_busy','md_queued','md_queue_limit','capacity_profile','capacity_revision'}
    local function copy(v)if type(v)~='table'then return v end;local out={};for k,x in pairs(v)do out[k]=copy(x)end;return out end
    local function read_current()
        assert(not retired and not fault,'placement retired/faulted')
        local s=c.public_current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=512,'canonical identity')
        local out={}
        for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            assert(not stamp or stamp[k]==s[k],'identity changed');out[k]=s[k]
        end
        local t=c.now();assert(finite(t) and t>=0 and (not last_clock or t>=last_clock),'UI clock invalid/reversed')
        stamp=out;last_clock=t;return out,t
    end
    local function current()
        local ok,s,t=pcall(read_current)
        if not ok then
            -- A failure observed after first binding is permanent, including
            -- failures inside a resolver before a pending callback exists.
            -- The caller owns pending settlement; do not invoke it twice here.
            if stamp then fault=tostring(s)end
            error(s)
        end
        return s,t
    end
    local function result(status,reason,r,cached)
        local o={scope='DIAGNOSTIC_SECTOR_POSE_ONLY',flight_verified=false,collision_safe=false,
            rendered_pixels_verified=false,runtime_verified=false,native_observation_cached=cached==true,
            capacity={profile=profile,revision=caprevision,source_sha256=capsource,history_used=count,history_limit=max_records,
                calls_used=serial,calls_limit=max_calls,scope='LOCAL_COUNTERS_AT_RESULT'},
            correlation_scope='FULL_MD_HEADER_VALIDATED_AGAINST_SDK_REQUEST_AND_PUBLIC_STAMP'}
        if r and r.last then
            -- The enclosing SDK receipt owns id/digest/session and the bound router
            -- owns the full public stamp. Keep the MD revision/call/phase/age, but
            -- do not repeat those already checked headers in its nested payload.
            local omitted={id=true,digest=true,session=true,generation=true,world_token=true,public_revision=true,reason=true}
            o.snapshot={};for k,v in pairs(r.last)do if not omitted[k]then o.snapshot[k]=copy(v)end end
            if r.last.kind=='SHIP_PLACEMENT_CAPACITY'then
                o.capacity_observation_cost={sdk_records=1,lua_records=1,native_records=0,native_calls=1}
            end
        end
        local out={status=status,reason=tostring(reason):sub(1,256),observations=o}
        assert(#d.encode({payload=d.encode(out),reserved=string.rep('x',900)})<=4000,'bounded placement reply')
        return out
    end
    local function fail(reason)
        fault=tostring(reason);local p=pending;pending=nil
        if p then p.r.uncertain=true;p.done(result('unknown','transport unavailable; no replay',nil))end
    end
    local function target(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued ship token')
        local id=d.resolve_issued(token,'ship')
        assert(type(id)=='cdata' and ffi.istype('uint64_t',id) and id~=0,'exact issued uint64')
        local text=c.component_id(id);assert(text==tostring(id),'native target conversion drift')
        return id,text
    end
    local function check(req,r)
        local s,t=current()
        assert(type(req)=='table' and req.provider=='x4-diagnostic-shipplacement-v1' and req.binding==d.revision
            and req.session==s.session and hex(req.id,32) and hex(req.digest,64),'request identity')
        assert(req.action=='diagnostic_place_ship' or req.action=='capacity_observe','fixed action')
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'plain arguments')
        local expected={}
        if req.action=='diagnostic_place_ship'then
            expected.ship=true
            assert(type(a.ship)=='string','issued ship required')
            for i,k in ipairs(parameters)do
                expected[k]=true;assert(finite(a[k]) and math.abs(a[k])<=(i<=3 and 10000000 or 360),'coordinate/angle bounds')
            end
        end
        for k in pairs(a)do assert(expected[k],'extra argument')end
        if r then
            assert(r.digest==req.digest and r.session==req.session and r.action==req.action,'changed request')
            for k,v in pairs(r.args)do assert(a[k]==v,'changed prepared arguments')end
        end
        return s,t,a
    end
    local function sequence(v,n)
        assert(type(v)=='table' and getmetatable(v)==nil and #v==n,'pose list shape')
        local count=0;for k in pairs(v)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=n,'pose list key');count=count+1 end
        assert(count==n,'pose list count')
        for i,x in ipairs(v)do assert(finite(x) and math.abs(x)<=(i<=3 and 20000000 or 1.001),'pose value')end
    end
    local function distance(a,b,first)
        local sum=0;for i=first,first+2 do sum=sum+(a[i]-b[i])^2 end;return math.sqrt(sum)
    end
    local function validate(row,p)
        assert(type(row)=='table' and getmetatable(row)==nil,'reply row')
        local usage=p.phase=='capacity';local fields={};local n=0
        for _,k in ipairs(common)do fields[k]=true;n=n+1 end
        for _,k in ipairs(usage and usagefields or nativefields)do fields[k]=true;n=n+1 end
        local count=0;for k in pairs(row)do assert(fields[k],'unknown reply field');count=count+1 end
        assert(count==n,'missing reply field')
        assert(row.kind==(usage and 'SHIP_PLACEMENT_CAPACITY' or 'SHIP_PLACEMENT') and row.revision==d.md_revision
            and row.id==p.r.id and row.digest==p.r.digest and row.call==p.call and row.phase==p.phase,'reply correlation')
        for _,k in ipairs({'session','generation','world_token'})do assert(row[k]==stamp[k],'reply world')end
        assert(row.public_revision==stamp.revision and finite(row.age) and row.age>=0,'native identity/age')
        assert(not p.r.last or row.age>=p.r.last.age,'native clock regressed')
        assert(type(row.reason)=='string' and #row.reason<=256,'reason bound')
        local out={};for k,v in pairs(row)do out[k]=v end
        if usage then
            assert(row.status=='observed' and row.reason=='READ_ONLY_CAPACITY_SAMPLE' and row.capacity_profile==profile
                and row.capacity_revision==caprevision and row.md_limit==max_records and row.md_queue_limit==64,'capacity source identity')
            assert(type(row.md_busy)=='boolean','native busy flag')
            for k,limit in pairs({md_used=max_records,md_queued=63})do
                assert(finite(row[k]) and row[k]%1==0 and row[k]>=0 and row[k]<=limit,'native count')
            end
            out.queue_count_scope='BEFORE_OWN_REPLY_APPEND';return out
        end
        assert(row.status=='prepared' or row.status=='refused' or row.status=='dispatched','native status')
        for _,k in ipairs({'target_exists','pose_observed','eligible','position_match','orientation_match','dispatched','consumed','busy'})do
            assert(type(row[k])=='boolean','native boolean')
        end
        assert(finite(row.speed_mps) and row.speed_mps>=0,'speed value')
        assert(not row.dispatched or row.consumed,'dispatch without consume')
        if row.status=='prepared'then assert(row.eligible and row.pose_observed and not row.dispatched and not row.consumed,'contradictory prepared')end
        if row.status=='dispatched'then assert(row.dispatched and row.consumed,'missing dispatch')end
        if p.phase=='prepare'then assert(not row.dispatched and not row.consumed,'prepare effect')end
        assert(not p.r.dispatched or row.dispatched,'lost native dispatch')
        out.target=row.target_exists and c.component_id(row.target) or ''
        assert(not row.target_exists or out.target==p.r.target_text,'target echo mismatch')
        if row.pose_observed then
            assert(row.target_exists,'pose without target');sequence(row.pose,12);sequence(row.desired,12)
            out.sector=c.component_id(row.sector)
            assert(not p.r.sector or out.sector==p.r.sector,'prepared sector drift')
            for i=1,3 do assert(row.desired[i]==p.r.args[parameters[i]],'requested position echo')end
            assert(not p.r.desired or d.encode(p.r.desired)==d.encode(row.desired),'native desired basis drift')
            assert(not row.position_match or distance(row.pose,row.desired,1)<=1.000000001,'false position match')
            for _,i in ipairs({4,7,10})do
                assert(not row.orientation_match or distance(row.pose,row.desired,i)<=0.001000001,'false basis match')
            end
        else sequence(row.pose,0);out.sector='';sequence(row.desired,#row.desired==0 and 0 or 12)end
        assert(not (row.position_match or row.orientation_match) or
            (p.phase=='status' and row.dispatched and row.pose_observed and row.eligible and row.speed_mps<=1),'unsupported achieved-pose claim')
        if row.position_match and row.orientation_match then
            assert(p.r.dispatched and p.r.dispatch_age and row.age>p.r.dispatch_age,'later native observation required')
        end
        out.pose=copy(row.pose);out.desired=copy(row.desired)
        out.pose_layout='SECTOR_XYZ_METRES_FORWARD_RIGHT_UP_NATIVE_BASIS';out.desired_scope='REQUEST_DERIVED_NATIVE_ROTATION'
        out.speed_scope=row.pose_observed and 'NATIVE_CLUSTER_RELATIVE_METRES_PER_SECOND' or 'UNAVAILABLE'
        return out
    end
    local function drain()
        if retired or fault or not stamp then return end
        local ok,err=pcall(function()
            current();local rows=c.read();c.clear();current()
            assert(type(rows)=='table' and getmetatable(rows)==nil,'batch shape')
            local n=0;for k in pairs(rows)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=64,'batch index');n=n+1 end
            assert(n==#rows and n<=64,'batch count');if n==0 then return end
            assert(n==1 and pending,'unsolicited/duplicate reply')
            local p=pending;local value=validate(rows[1],p);local _,ended=current()
            assert(ended-p.started<=10,'reply deadline')
            local status=value.status
            if status=='observed'then status='completed'
            elseif status=='refused' and value.dispatched then status='unknown'
            elseif status=='dispatched'then status=value.position_match and value.orientation_match and 'completed' or 'running'end
            local response=result(status,value.reason,{last=value});local _,finished=current()
            assert(finished-p.started<=10,'reply encoding deadline')
            pending=nil;p.r.last=value;p.r.dispatched=value.dispatched==true
            if value.status=='prepared'then p.r.sector=value.sector;p.r.desired=copy(value.desired)end
            if p.phase=='execute' and value.dispatched then p.r.dispatch_age=value.age end
            p.done(response)
        end)
        if not ok then fail(err)end
    end
    function api.tick()
        if retired or fault then return end
        local ok,err=pcall(function()local _,t=current();assert(not pending or t-pending.started<=10,'MD deadline; no replay')end)
        if not ok and stamp then fail(err)end
    end
    local function send(phase,req,r,done)
        assert(not pending and not r.uncertain and serial<max_calls,'pending/fault/call capacity')
        local s,t=current();local object
        if phase~='capacity'then local key;object,key=target(r.args.ship);assert(key==r.target_text,'issued target changed')end
        local _,final=current()
        if phase=='execute' or phase=='capacity'then assert(final-r.prepared<=10,'pre-send preparation expired')end
        serial=serial+1
        local body={revision=d.md_revision,id=req.id,digest=req.digest,call=string.format('%032x',serial),session=s.session,
            generation=s.generation,world_token=s.world_token,public_revision=s.revision,target=object}
        for _,k in ipairs(parameters)do body[k]=r.args[k]end
        pending={phase=phase,r=r,done=done,call=body.call,started=t}
        if phase=='execute' or phase=='capacity'then r.sent=true end
        c.send(phase,body)
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback');local r=type(req)=='table' and records[req.id] or nil
        local delivered=false
        local function once(v)assert(not delivered,'duplicate delivery');delivered=true;done(v)end
        local ok,err=pcall(function()
            local _,t,a=check(req,r)
            if phase=='prepare'then
                assert(not r and not pending and count<max_records,'duplicate/busy/history capacity')
                local key;if req.action=='diagnostic_place_ship'then local object;object,key=target(a.ship)end
                current();r={id=req.id,digest=req.digest,session=req.session,action=req.action,args=copy(a),target_text=key,prepared=t}
                records[req.id]=r;count=count+1
                if req.action=='capacity_observe'then return once(result('prepared','read-only capacity preparation',r))end
                return send('prepare',req,r,once)
            end
            assert(r,'missing preparation')
            if req.action=='capacity_observe'then
                if phase=='execute'then assert(not r.sent and t-r.prepared<=10,'consumed/stale sample');return send('capacity',req,r,once)end
                assert(phase=='status','unknown phase')
                return once(result(r.last and 'completed' or r.sent and 'unknown' or 'prepared','retained capacity; no resend',r,true))
            end
            if pending then return once(result('running','MD reply pending; no resend',r,true))end
            if phase=='execute'then assert(not r.sent and r.last and r.last.status=='prepared' and t-r.prepared<=10,'not prepared/consumed/stale')
            else assert(phase=='status','unknown phase')end
            send(phase,req,r,once)
        end)
        if not ok then
            if pending and pending.r==r then fail(err)
            elseif not delivered then once(result('unknown',err,nil))end
        end
    end
    function api.dispose()
        if dispose_failure then return false end;if retired then return true end;retired=true
        if pending then pending.r.uncertain=true;pending=nil end
        local ok,v=pcall(c.dispose);if not ok or v~=true then dispose_failure=true;return false end;return true
    end
    api.spec={id='x4-diagnostic-shipplacement-v1',revision=d.revision,actions={diagnostic_place_ship=true,capacity_observe=true},
        session=function()api.tick();local ok,s=pcall(current);return ok and s.session or nil end,invoke=invoke,dispose=api.dispose}
    local ok,err=pcall(function()c.arm_tick(api.tick);c.subscribe(drain)end)
    if not ok then api.dispose();error(err)end
    return api
end

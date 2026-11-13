-- Fixed native OrbitParameterized route. No world-pose or distance verdict.
return function(d)
    assert(type(d)=='table' and d.ffi and type(d.channel)=='table','orbit dependencies')
    local c,ffi=d.channel,d.ffi
    for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
        assert(type(c[k])=='function','channel method '..k)
    end
    assert(type(d.encode)=='function' and type(d.resolve_issued)=='function','native dependencies')
    local profile_name=d.capacity_profile
    if profile_name==nil then profile_name='interactive' end
    assert(profile_name=='interactive' or profile_name=='campaign_v1','capacity profile')
    local caps
    if d.capacity_resolver==nil then
        assert(profile_name=='interactive','campaign requires source-bound capacity resolver')
        caps={name='interactive',orbit_history=64,orbit_calls=256}
    else
        assert(type(d.capacity_resolver)=='function','capacity resolver')
        caps=d.capacity_resolver(profile_name)
    end
    assert(type(caps)=='table' and getmetatable(caps)==nil and caps.name==profile_name,'capacity identity')
    for _,k in ipairs({'orbit_history','orbit_calls'})do
        assert(type(caps[k])=='number' and caps[k]>=1 and caps[k]<=16384 and caps[k]%1==0,'capacity bound')
    end
    local max_records,max_calls=caps.orbit_history,caps.orbit_calls
    local capacity_revision,capacity_source=caps.revision,caps.source_sha256
    if d.capacity_resolver==nil then
        capacity_revision,capacity_source='UNBOUND_LEGACY_INTERACTIVE','UNBOUND_LEGACY_INTERACTIVE'
    else
        for _,v in ipairs({capacity_revision or false,capacity_source or false})do
            assert(type(v)=='string' and #v==64 and not v:find('[^0-9a-f]'),'capacity source identity')
        end
    end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    assert(hex(d.revision,64) and hex(d.md_revision,64),'source revisions')
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<=9007199254740991 end
    local function copy(v)
        if type(v)~='table'then return v end
        local out={};for k,x in pairs(v)do out[k]=copy(x)end;return out
    end
    local api,records,count,serial={}, {},0,0
    function api.capacity()
        return {profile=profile_name,revision=capacity_revision,source_sha256=capacity_source,
            history_used=count,history_limit=max_records,
            calls_used=serial,calls_limit=max_calls,scope='LOCAL_COUNTERS_AT_RESULT_NOT_NATIVE_CENSUS'}
    end
    local stamp,last_clock,pending,fault,retired,dispose_failure=nil,nil,nil,nil,false,false
    local fields={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision',
        'status','reason','age','consumed','dispatched','handle','stopped','stop_requested','target','yaw','pitch','zoom','external','fullscreen'}
    local usage_fields={'md_used','md_limit','md_busy','md_queued','md_queue_limit','capacity_profile','capacity_revision'}
    local actions={camera_observe='observe',orbit_start='start',follow_start='follow',orbit_stop='stop'}
    local function starts(op)return op=='start' or op=='follow'end
    local function current()
        assert(not retired and not fault,'orbit retired or faulted')
        local s=c.public_current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=512,'canonical identity')
        local x={};for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            if stamp then assert(s[k]==stamp[k],'identity changed')end;x[k]=s[k]
        end
        stamp=x;local now=c.now()
        assert(finite(now) and now>=0 and (not last_clock or now>=last_clock),'invalid clock')
        last_clock=now;return s,now
    end
    local function result(status,reason,r)
        local o={scope='NATIVE_ORBIT_LIFECYCLE_AND_RAW_CAMERA_PROPERTIES',runtime_verified=false,
            achieved_pose_verified=false,target_distance_verified=false,angle_units='UNKNOWN',reference_frame='UNKNOWN',
            start_minimum_delay_sim_seconds=1.2,minimum_delay_domain='MD_PLAYER_AGE',atomic=false,capacity=api.capacity()}
        if r and r.last then o.snapshot=copy(r.last);o.native_observation_cached=r.cached==true end
        local out={status=status,reason=reason,observations=o}
        assert(#d.encode({payload=d.encode(out),reserved=string.rep('x',900)})<=4000,'reply bound')
        return out
    end
    local function fail(reason)
        fault=tostring(reason);local p=pending;pending=nil
        if p then p.r.uncertain=true;p.done(result('unknown','native response unavailable; no retry',p.r))end
    end
    local function target(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued ship token')
        local id=d.resolve_issued(token,'ship')
        assert(type(id)=='cdata' and ffi.istype('uint64_t',id) and id~=0,'exact issued ship')
        local text=c.component_id(id);assert(type(text)=='string' and text==tostring(id),'native ID conversion drift')
        return id,text
    end
    local function arguments(req)
        local op=actions[req.action];assert(op,'closed action vocabulary')
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'arguments')
        local allowed=op=='start' and {ship=true,distance=true,elevation=true,duration=true}
            or op=='follow' and {ship=true,distance=true,yaw=true,pitch=true,duration=true} or op=='stop' and {owner=true} or {}
        local n=0;for k in pairs(a)do assert(allowed[k],'closed arguments');n=n+1 end
        assert(n==(op=='start' and 4 or op=='follow' and 5 or op=='stop' and 1 or 0),'argument denominator')
        if starts(op)then
            target(a.ship)
            local numbers=op=='start' and {'distance','elevation','duration'} or {'distance','yaw','pitch','duration'}
            for _,k in ipairs(numbers)do assert(finite(a[k]) and a[k]%1==0,'integral bounded parameter')end
            assert(a.distance>=10 and a.distance<=100000
                and a.duration>=1 and a.duration<=30,'orbit bounds')
            if op=='start'then assert(math.abs(a.elevation)<=100000,'elevation bounds')
            else assert(math.abs(a.yaw)<=360 and math.abs(a.pitch)<=90,'follow angle bounds')end
        elseif op=='stop'then assert(hex(a.owner,32),'local owner request ID')end
        return op,a
    end
    local function check(req,r)
        local s,now=current()
        assert(type(req)=='table' and req.provider=='x4-orbit-camera-v1' and req.binding==d.revision
            and req.session==s.session and hex(req.id,32) and hex(req.digest,64),'request binding')
        local op,a=arguments(req)
        if r then
            assert(req.digest==r.digest and req.session==r.session and op==r.op,'request changed')
            for k,v in pairs(r.args)do assert(a[k]==v,'arguments changed')end
        end
        return s,now,op,a
    end
    -- MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0 (vanilla
    -- ego_detailmonitorhelper/helper.lua:8824 tests a blackboard flag with ~= 0). Exactly 1/0
    -- of the NAMED reply fields become booleans; any other value is left for the checks to refuse.
    local function md_flags(row,names)
        for _,k in ipairs(names) do
            if row[k]==1 then row[k]=true elseif row[k]==0 then row[k]=false end
        end
    end
    local function validate(row,p)
        assert(type(row)=='table' and getmetatable(row)==nil,'reply row')
        md_flags(row,{'consumed','dispatched','stopped','stop_requested','external','fullscreen','md_busy'})
        local expected={};for _,k in ipairs(fields)do expected[k]=true end
        local denominator=#fields
        if p.phase=='snapshot'then
            for _,k in ipairs(usage_fields)do expected[k]=true end;denominator=denominator+#usage_fields
        end
        local n=0;for k in pairs(row)do assert(expected[k],'unknown reply field');n=n+1 end
        assert(n==denominator,'reply denominator')
        assert(row.kind=='ORBIT' and row.revision==d.md_revision and row.id==p.r.id and row.digest==p.r.digest
            and row.call==p.call and row.phase==p.phase,'reply correlation')
        for _,k in ipairs({'session','generation','world_token'})do assert(row[k]==stamp[k],'reply identity')end
        assert(row.public_revision==stamp.revision,'reply public revision')
        assert(type(row.reason)=='string' and #row.reason<=256,'reason bound')
        assert(row.status=='prepared' or row.status=='refused' or row.status=='requested'
            or row.status=='stop_requested' or row.status=='observed','reply status')
        assert(finite(row.age) and row.age>=0 and (not p.r.last or row.age>=p.r.last.age),'native age')
        for _,k in ipairs({'consumed','dispatched','stopped','stop_requested','external','fullscreen'})do
            assert(type(row[k])=='boolean','reply boolean')
        end
        assert(finite(row.yaw) and finite(row.pitch) and finite(row.zoom) and row.zoom>=0,'raw camera sample')
        assert(type(row.handle)=='string' and #row.handle<=64 and not row.handle:find('[%c]'),'opaque handle text')
        assert(not row.dispatched or row.consumed,'dispatch before consumption')
        if row.status=='prepared'then assert(not row.consumed and not row.dispatched,'preparation contradiction')end
        if row.status=='requested' or row.status=='stop_requested'then assert(row.dispatched and row.consumed,'dispatch missing')end
        if p.phase=='prepare'then assert(not row.dispatched and not row.consumed,'prepare side effect')end
        assert((row.status=='observed')==(p.phase=='snapshot'),'snapshot phase/status differs')
        if p.phase=='snapshot'then
            assert(row.capacity_profile==profile_name and row.capacity_revision==capacity_revision,'native capacity identity')
            assert(row.md_limit==max_records and finite(row.md_used) and row.md_used%1==0
                and row.md_used>=0 and row.md_used<=row.md_limit,'native record count')
            assert(row.md_queue_limit==64 and finite(row.md_queued) and row.md_queued%1==0
                and row.md_queued>=0 and row.md_queued<64 and type(row.md_busy)=='boolean','native queue/busy')
        end
        if row.dispatched then assert(row.handle~='','dispatched without owned handle')end
        if row.stopped then assert(row.handle~='','stopped without handle')end
        local out=copy(row)
        if p.r.target_text then
            if row.status~='refused'then assert(c.component_id(row.target)==p.r.target_text,'target echo changed')end
            out.target=row.status=='refused' and 'UNKNOWN' or p.r.target_text
        else assert(row.target==0 and row.handle=='' and not row.dispatched and not row.consumed
            and not row.stopped and not row.stop_requested,'read-only snapshot side effect');out.target='NOT_REQUESTED' end
        if p.r.last and p.r.last.handle~='' then assert(row.handle==p.r.last.handle,'owned handle changed')end
        if p.r.dispatched then assert(row.dispatched,'dispatch state lost')end
        return out
    end
    local function drain()
        if retired or fault then return end
        local ok,why=pcall(function()
            current();local rows=c.read();c.clear();current()
            assert(type(rows)=='table' and getmetatable(rows)==nil,'batch')
            local n=0;for k in pairs(rows)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=64,'batch index');n=n+1 end
            assert(n==#rows and n<=64,'batch denominator');if n==0 then return end
            assert(n==1 and pending,'unsolicited/duplicate batch')
            local p=pending;local value=validate(rows[1],p);local _,ended=current()
            assert(ended-p.started<=10,'response validation deadline')
            pending=nil;p.r.last=value;p.r.dispatched=value.dispatched
            local status=value.status
            if status=='observed'then status='completed'
            elseif (status=='requested' or status=='stop_requested') and value.dispatched and value.stopped then status='completed'
            elseif status=='refused' and value.dispatched then status='unknown'
            elseif status=='requested' or status=='stop_requested'then status='running' end
            p.done(result(status,value.reason,p.r))
        end)
        if not ok then fail(why)end
    end
    function api.tick()
        if retired or fault then return end
        local ok,why=pcall(function()local _,now=current();if pending then assert(now-pending.started<=10,'response timeout')end end)
        if not ok and stamp then fail(why)end -- unbound: nothing to time out yet
    end
    local function send(phase,req,r,done)
        assert(not pending and not r.uncertain,'unresolved native response')
        local s,now=current();local id=0
        if starts(r.op)then local text;id,text=target(r.args.ship);assert(text==r.target_text,'target changed')end
        current();assert(serial<max_calls,'call capacity');serial=serial+1
        local call=string.format('%032x',serial)
        local body={revision=d.md_revision,id=req.id,digest=req.digest,call=call,op=r.op,
            session=s.session,generation=s.generation,world_token=s.world_token,public_revision=s.revision,
            target=id,distance=r.args.distance or 0,elevation=r.args.elevation or 0,yaw=r.args.yaw or 0,pitch=r.args.pitch or 0,
            duration=r.args.duration or 0,owner=r.args.owner or ''}
        pending={phase=phase,r=r,done=done,call=call,started=now}
        if phase=='execute' or phase=='snapshot'then r.sent=true end
        c.send(phase,body)
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback')
        local r=type(req)=='table' and records[req.id] or nil;local delivered=false
        local function once(v)assert(not delivered,'duplicate callback');delivered=true;done(v)end
        local ok,why=pcall(function()
            local _,now,op,a=check(req,r)
            if phase=='prepare'then
                assert(not r and count<max_records and not pending,'duplicate/busy/capacity')
                r={id=req.id,digest=req.digest,session=req.session,op=op,args=copy(a),prepared=now,sent=false}
                if starts(op)then local _,text=target(a.ship);r.target_text=text
                elseif op=='stop'then
                    local owner=records[a.owner]
                    assert(owner and starts(owner.op) and owner.dispatched and owner.last and owner.last.handle~=''
                        and not owner.uncertain and not owner.last.stopped and not owner.last.stop_requested,'own observed start required')
                    r.target_text=owner.target_text
                end
                current();records[req.id]=r;count=count+1
                if op=='observe'then return once(result('prepared','read-only snapshot prepared',r))end
                return send('prepare',req,r,once)
            end
            assert(r,'preparation missing')
            if pending then return once(result('running','native reply pending; no resend',r))end
            if phase=='execute'then
                assert(not r.sent and (op=='observe' or r.last and r.last.status=='prepared') and now-r.prepared<=10,'not prepared/consumed/expired')
                return send(op=='observe' and 'snapshot' or 'execute',req,r,once)
            end
            assert(phase=='status','unknown phase')
            if op=='observe' and r.last then return once(result('completed','historical read-only sample',{last=r.last,cached=true}))end
            assert(op~='observe','snapshot not executed')
            send('status',req,r,once)
        end)
        if not ok then
            if pending and pending.r==r then fail(why)
            elseif not delivered then once(result('unknown',tostring(why):sub(1,256),r))end
        end
    end
    function api.dispose()
        if dispose_failure then return false end
        if retired then return true end;retired=true
        if pending then pending.r.uncertain=true;pending=nil end
        local ok,v=pcall(c.dispose);if not ok or v~=true then dispose_failure=true;return false end
        return true
    end
    api.spec={id='x4-orbit-camera-v1',revision=d.revision,
        actions={camera_observe=true,orbit_start=true,follow_start=true,orbit_stop=true},
        session=function()api.tick();local ok,s=pcall(current);return ok and s.session or nil end,
        invoke=invoke,dispose=api.dispose}
    local ok,why=pcall(function()c.arm_tick(api.tick);c.subscribe(drain)end)
    if not ok then api.dispose();error(why)end
    return api
end

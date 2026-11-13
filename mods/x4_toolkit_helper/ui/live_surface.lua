-- One read-only native surface ray. No containment/collision verdict or movement.
return function(d)
    assert(type(d)=='table' and d.ffi and type(d.channel)=='table','surface dependencies')
    local c,ffi=d.channel,d.ffi
    for _,k in ipairs({'public_current','now','send','read','clear','subscribe','arm_tick','component_id','dispose'})do
        assert(type(c[k])=='function','surface channel '..k)
    end
    for _,k in ipairs({'resolve_issued','encode','capacity_resolver'})do assert(type(d[k])=='function','surface '..k)end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<math.huge end
    assert(hex(d.revision,64) and hex(d.md_revision,64),'source revisions')
    local profile=d.capacity_profile;if profile==nil then profile='interactive' end
    assert(profile=='interactive' or profile=='campaign_v1','fixed profile')
    local cap=d.capacity_resolver(profile)
    assert(type(cap)=='table' and cap.name==profile and hex(cap.revision,64) and hex(cap.source_sha256,64)
        and cap.native_history==(profile=='interactive' and 128 or 512),'canonical capacity')
    local limit,call_limit=cap.native_history,cap.native_history*8
    local faces={xplus=true,xminus=true,yplus=true,yminus=true,zplus=true,zminus=true}
    local api,records,count,serial={}, {},0,0
    local stamp,clock,pending,fault,retired,dispose_failed=nil,nil,nil,nil,false,false
    local function copy(v)if type(v)~='table'then return v end;local o={};for k,x in pairs(v)do o[k]=copy(x)end;return o end
    local function read_current()
        assert(not retired and not fault,'surface retired/faulted')
        local s=c.public_current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=512,'canonical identity unavailable')
        local out={}
        for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            assert(not stamp or stamp[k]==s[k],'public identity changed');out[k]=s[k]
        end
        local now=c.now();assert(finite(now) and now>=0 and (not clock or now>=clock),'UI clock reversed/unavailable')
        stamp=out;clock=now;return out,now
    end
    local function current()
        local ok,s,t=pcall(read_current)
        if not ok then
            if stamp then fault=tostring(s)end
            error(s)
        end
        return s,t
    end
    local function target(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued ship token')
        local ok,v=pcall(d.resolve_issued,token,'ship');current()
        assert(ok,v)
        assert(type(v)=='cdata' and ffi.istype('uint64_t',v) and v~=0,'exact issued ship required')
        local converted,text=pcall(c.component_id,v);current()
        assert(converted,text);assert(text==tostring(v),'component conversion drift')
        return v,text
    end
    local function result(status,reason,r,cached)
        local o={scope='ONE_NATIVE_SURFACE_QUERY',inside_hull='UNKNOWN',complete_collision_coverage=false,
            collision_safe=false,rendered_pixels_verified=false,runtime_case_pass=false,
            native_observation_cached=cached==true,
            capacity={profile=profile,revision=cap.revision,history_used=count,history_limit=limit,
                calls_used=serial,calls_limit=call_limit}}
        if r and r.last then
            local omitted={id=true,digest=true,session=true,generation=true,world_token=true,public_revision=true,reason=true}
            o.snapshot={};for k,v in pairs(r.last)do if not omitted[k]then o.snapshot[k]=copy(v)end end
        end
        local out={status=status,reason=tostring(reason):sub(1,160),observations=o}
        assert(#d.encode({payload=d.encode(out),reserved=string.rep('x',900)})<=4000,'bounded surface reply')
        return out
    end
    local function fail(why)
        fault=tostring(why);local p=pending;pending=nil
        if p then
            p.r.uncertain=true
            local ok,err=pcall(p.done,result('unknown','transport/correlation failed; no replay',nil))
            if not ok then fault=fault..'; callback delivery failed: '..tostring(err)end
        end
    end
    local function array(v,n,bound)
        assert(type(v)=='table' and getmetatable(v)==nil and #v==n,'numeric list shape')
        local count=0;for k,x in pairs(v)do
            assert(type(k)=='number' and k%1==0 and k>=1 and k<=n and finite(x) and math.abs(x)<=bound,'numeric list value/key')
            count=count+1
        end
        assert(count==n,'sparse numeric list')
    end
    local function basis(v,start)
        for i=start,start+8,3 do
            local length=0;for k=i,i+2 do length=length+v[k]*v[k]end
            assert(math.abs(length-1)<=0.003,'nonunit native basis')
            for j=i+3,start+8,3 do
                local dot=0;for k=0,2 do dot=dot+v[i+k]*v[j+k]end
                assert(math.abs(dot)<=0.003,'nonorthogonal native basis')
            end
        end
    end
    local common={'kind','revision','id','digest','call','phase','session','generation','world_token','public_revision','age','status','reason'}
    local sample={'target','target_exists','sector','physics_ready','sample_observed','surface_found','face','origin','surface_position','surface_frame','host_pose','bbox_centre','dimensions'}
    local usage={'md_used','md_limit','md_queued','md_queue_limit','capacity_profile','capacity_revision'}
    local function validate(v,p)
        assert(type(v)=='table' and getmetatable(v)==nil,'plain native reply')
        local names,n={},0
        for _,group in ipairs({common,p.phase=='capacity' and usage or sample})do
            for _,k in ipairs(group)do names[k]=true;n=n+1 end
        end
        local found=0;for k in pairs(v)do assert(names[k],'unknown native reply field');found=found+1 end
        assert(found==n,'missing native reply field')
        assert(v.kind==(p.phase=='capacity' and 'SURFACE_CAPACITY' or 'SURFACE') and v.revision==d.md_revision
            and v.id==p.r.id and v.digest==p.r.digest and v.call==p.call and v.phase==p.phase,'native reply identity')
        for _,k in ipairs({'session','generation','world_token'})do assert(v[k]==stamp[k],'native world mismatch')end
        assert(v.public_revision==stamp.revision and finite(v.age) and v.age>=0,'native revision/age')
        assert(type(v.reason)=='string' and #v.reason<=256,'native reason bound')
        local out=copy(v)
        if p.phase=='capacity'then
            assert(v.status=='observed' and v.reason=='READ_ONLY_CAPACITY_SAMPLE' and v.capacity_profile==profile
                and v.capacity_revision==cap.revision and v.md_limit==limit and v.md_queue_limit==64,'capacity identity')
            for k,max in pairs({md_used=limit,md_queued=63})do
                assert(finite(v[k]) and v[k]%1==0 and v[k]>=0 and v[k]<=max,'native capacity count')
            end
            out.queue_count_scope='BEFORE_OWN_REPLY_APPEND';return out
        end
        assert(v.status=='observed' or v.status=='refused','native sample status')
        for _,k in ipairs({'target_exists','physics_ready','sample_observed','surface_found'})do assert(type(v[k])=='boolean','native boolean')end
        assert(v.face==p.r.args.face,'face echo mismatch')
        assert((v.status=='observed')==v.sample_observed,'contradictory native observation')
        out.target=v.target_exists and c.component_id(v.target) or ''
        assert(not v.target_exists or out.target==p.r.target_text,'target echo mismatch')
        out.sector=v.target_exists and c.component_id(v.sector) or ''
        for k,size in pairs({origin=3,bbox_centre=3,dimensions=3,host_pose=12})do
            local length=type(v[k])=='table' and #v[k] or -1
            assert(length==size or (not v.sample_observed and length==0),'sample array length')
            array(v[k],length,20000000)
        end
        if v.sample_observed then
            assert(v.target_exists and v.physics_ready,'observation without live physics')
            for _,x in ipairs(v.dimensions)do assert(x>0 and x<=100000,'dimension bounds')end
            for i=4,12 do assert(math.abs(v.host_pose[i])<=1.001,'host basis component')end
            basis(v.host_pose,4)
        end
        array(v.surface_position,v.surface_found and 3 or 0,20000000)
        array(v.surface_frame,v.surface_found and 9 or 0,1.001)
        if v.surface_found then assert(v.target_exists and v.physics_ready,'surface without physics');basis(v.surface_frame,1)end
        out.position_frame='TARGET_OBJECT_LOCAL_XYZ_METRES'
        out.origin_scope='REQUEST_DERIVED_FROM_NATIVE_BBOX'
        out.face_scope='CALLER_REQUESTED_FIXED_DIRECTION'
        out.surface_frame_scope='NATIVE_RETURNED_FORWARD_RIGHT_UP_BASIS'
        out.host_pose_frame='SECTOR_XYZ_METRES_FORWARD_RIGHT_UP_BASIS'
        return out
    end
    local function drain()
        if retired or fault or not stamp then return end
        local ok,why=pcall(function()
            current();local rows=c.read();c.clear();current()
            assert(type(rows)=='table' and getmetatable(rows)==nil,'reply batch shape')
            local n=0;for k in pairs(rows)do assert(type(k)=='number' and k%1==0 and k>=1 and k<=64,'batch index');n=n+1 end
            assert(n==#rows and n<=64,'reply batch count')
            if n==0 then return end
            assert(n==1 and pending,'unsolicited/duplicate native reply')
            local p=pending;local value=validate(rows[1],p);local _,ended=current()
            assert(ended-p.started<=10,'reply deadline')
            local out=result(value.status=='observed' and 'completed' or 'refused',value.reason,{last=value})
            local _,finished=current();assert(finished-p.started<=10,'encoded reply deadline')
            pending=nil;p.r.last=value;p.done(out)
        end)
        if not ok then fail(why)end
    end
    function api.tick()
        if retired or fault then return end
        local ok,why=pcall(function()local _,now=current();assert(not pending or now-pending.started<=10,'native deadline')end)
        if not ok and stamp then fail(why)end
    end
    local function check(req,r)
        local s,t=current()
        assert(type(req)=='table' and req.provider=='x4-native-surface-v1' and req.binding==d.revision
            and req.session==s.session and hex(req.id,32) and hex(req.digest,64),'request binding')
        assert(req.action=='surface_sample' or req.action=='capacity_observe','closed surface action')
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'plain arguments')
        if req.action=='surface_sample'then
            assert(type(a.ship)=='string' and faces[a.face],'issued ship and fixed face')
            for k in pairs(a)do assert(k=='ship' or k=='face','extra sample argument')end
        else assert(next(a)==nil,'capacity takes no arguments')end
        if r then
            assert(r.digest==req.digest and r.session==req.session and r.action==req.action,'request changed')
            for k,v in pairs(r.args)do assert(a[k]==v,'arguments changed')end
        end
        return s,t,a
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback required')
        local r=type(req)=='table' and records[req.id] or nil;local delivered=false
        local function once(v)assert(not delivered,'duplicate callback');delivered=true;done(v)end
        local ok,why=pcall(function()
            local _,t,a=check(req,r)
            if phase=='prepare'then
                assert(not r and not pending and count<limit,'duplicate/busy/history capacity')
                local text;if req.action=='surface_sample'then local object;object,text=target(a.ship)end
                current();r={id=req.id,digest=req.digest,session=req.session,action=req.action,args=copy(a),prepared=t,target_text=text}
                records[req.id]=r;count=count+1
                return once(result('prepared','read-only sample prepared locally; no native query yet',r))
            end
            assert(r,'own preparation required')
            if phase=='status'then
                return once(result(r.last and (r.last.status=='observed' and 'completed' or 'refused')
                    or r.sent and 'unknown' or 'prepared','retained observation; no native resend',r,true))
            end
            assert(phase=='execute' and not r.sent and not r.uncertain and not pending and t-r.prepared<=10 and serial<call_limit,
                'consumed/stale/busy request')
            local object
            if req.action=='surface_sample'then
                local text;object,text=target(a.ship);assert(text==r.target_text,'issued target changed')
            end
            local s,ended=current();assert(ended-r.prepared<=10,'expired after target resolution')
            serial=serial+1;r.sent=true
            local body={revision=d.md_revision,id=r.id,digest=r.digest,call=string.format('%032x',serial),session=s.session,
                generation=s.generation,world_token=s.world_token,public_revision=s.revision,target=object,face=a.face}
            local native_phase=req.action=='capacity_observe' and 'capacity' or 'sample'
            pending={phase=native_phase,r=r,done=once,call=body.call,started=t}
            c.send(native_phase,body)
        end)
        if not ok then
            if pending and (pending.r==r or fault) then
                local same=pending.r==r
                fail(why)
                if not same and not delivered then once(result('refused',why,nil))end
            elseif not delivered then once(result(r and r.sent and 'unknown' or 'refused',why,nil))end
        end
    end
    function api.dispose()
        if dispose_failed then return false end;if retired then return true end;retired=true
        if pending then pending.r.uncertain=true;pending=nil end
        local ok,v=pcall(c.dispose);if not ok or v~=true then dispose_failed=true;return false end;return true
    end
    api.spec={id='x4-native-surface-v1',revision=d.revision,actions={surface_sample=true,capacity_observe=true},
        session=function()api.tick();local ok,s=pcall(current);return ok and s.session or nil end,invoke=invoke,dispose=api.dispose}
    local ok,why=pcall(function()c.arm_tick(api.tick);c.subscribe(drain)end)
    if not ok then api.dispose();error(why)end
    return api
end

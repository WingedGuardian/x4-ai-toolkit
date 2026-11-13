-- Fixed topology observation only. Native MD find defaults exclude non-normal
-- clusters; zones exclude highways. Ownerless gates are not filtered away.
return function(d)
    assert(type(d)=='table' and type(d.channel)=='table' and type(d.encode)=='function','topology dependencies')
    local c,encode,revision=d.channel,d.encode,d.revision
    local function hex(v,n) return type(v)=='string' and #v==n and not v:find('[^0-9a-f]') end
    assert(hex(revision,64),'topology revision required')
    for _,k in ipairs({'public_current','now','subscribe','arm_tick','send','read','clear','component_id','dispose'}) do
        assert(type(c[k])=='function','missing topology channel '..k)
    end
    local fields={'kind','provider','revision','request','session','generation','world_token','query_kind','sector_macro',
        'age_before','age_after','stable','status','total','scope_total','scope_matches','unknown','rows'}
    local row_fields={'object','exists','macro','cluster','cluster_macro','sector','sector_macro','zone','zone_macro',
        'active','exit','exit_macro','exit_sector','exit_sector_macro'}
    local kinds={clusters=true,sectors=true,zones=true,gates=true}
    local api,used,count={}, {},0
    local retired,fault,started=false,nil,false
    local pending,result,saved,stamp,last_clock=nil,nil,nil,nil,nil
    local last_native_age=0
    local function copy(t) local o={} for k,v in pairs(t) do o[k]=type(v)=='table' and copy(v) or v end return o end
    local function finite(v) return type(v)=='number' and v==v and v>=0 and v<math.huge end
    local function integer(v,a,b) return finite(v) and v%1==0 and v>=a and v<=b end
    local function text(v) return type(v)=='string' and #v<=512 and not v:find('%z') end
    local function shape(t,names)
        assert(type(t)=='table' and getmetatable(t)==nil,'plain native table required')
        local allowed={} for _,k in ipairs(names) do allowed[k]=true;assert(t[k]~=nil,'missing field '..k) end
        local n=0 for k in pairs(t) do assert(allowed[k],'unexpected native field');n=n+1 end
        assert(n==#names,'field denominator')
    end
    local function list(t,cap)
        assert(type(t)=='table' and getmetatable(t)==nil and #t<=cap,'bounded native list')
        local n=0 for k in pairs(t) do assert(integer(k,1,#t),'sparse native list');n=n+1 end
        assert(n==#t,'native list denominator');return t
    end
    local function same(a,b)
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do if a[k]~=b[k] then return false end end
        return true
    end
    local function current()
        assert(not retired and not fault,'topology retired/faulted')
        local s=c.public_current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and text(s.world_token) and #s.world_token>0,'public identity')
        assert(not stamp or same(s,stamp),'public identity changed')
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,world_token=s.world_token}
    end
    local function now()
        local v=c.now();assert(finite(v) and (not last_clock or v>=last_clock),'invalid/backward UI clock')
        last_clock=v;return v
    end
    local function fail(why)
        fault=tostring(why);pending=nil;saved=nil
        result={status='UNQUALIFIED',reason=fault,enumeration_complete=false,runtime_verified=false}
    end
    local function component(v)
        local k=type(v)
        assert(k=='number' or k=='string' or k=='cdata','component is not scalar')
        if k=='number' then assert(integer(v,1,9007199254740991),'unsafe numeric component') end
        if k=='string' then assert(#v<=64 and (v:match('^[1-9]%d*$') or v:match('^[1-9]%d*ULL$')),'invalid component text') end
        local out=c.component_id(v)
        assert(type(out)=='string','native component conversion unavailable')
        out=out:gsub('ULL$','')
        assert(out:match('^[1-9]%d*$') and #out<=20 and (#out<20 or out<='18446744073709551615'),'exact uint64 component required')
        return out
    end
    -- MD has no boolean datatype: an MD true/false reaches Lua as the number 1/0 (vanilla
    -- ego_detailmonitorhelper/helper.lua:8824 tests a blackboard flag with ~= 0). Exactly 1/0
    -- of the NAMED reply fields become booleans; any other value is left for the checks to refuse.
    local function md_flags(row,names)
        for _,k in ipairs(names) do
            if row[k]==1 then row[k]=true elseif row[k]==0 then row[k]=false end
        end
    end
    local function native_row(r,kind)
        shape(r,row_fields);md_flags(r,{'exists','active'});assert(type(r.exists)=='boolean' and text(r.macro),'row identity state')
        local out={exists=r.exists,macro=r.macro}
        if not r.exists then
            assert(r.object==0 and r.macro=='' and r.active==-1,'contradictory missing object')
            for _,k in ipairs({'cluster','sector','zone','exit','exit_sector'}) do
                assert(r[k]==0 and r[k..'_macro']=='','missing object retained context')
            end
            return out
        end
        assert(#r.macro>0,'missing object macro');out.id=component(r.object)
        for _,k in ipairs({'cluster','sector','zone','exit','exit_sector'}) do
            assert(text(r[k..'_macro']),'invalid context macro')
            if r[k]==0 then assert(r[k..'_macro']=='','absent context with macro');out[k]={present=false}
            else
                assert(#r[k..'_macro']>0,'present context missing macro')
                out[k]={present=true,id=component(r[k]),macro=r[k..'_macro']}
            end
        end
        local own=kind=='clusters' and 'cluster' or kind=='sectors' and 'sector' or kind=='zones' and 'zone'
        if own then assert(out[own].present and out[own].id==out.id and out[own].macro==r.macro,'self context mismatch') end
        assert(out.cluster.present,'cluster context unavailable')
        if kind~='clusters' then assert(out.sector.present,'sector context unavailable') end
        if kind=='gates' or kind=='zones' then assert(out.zone.present,'zone context unavailable') end
        if kind=='gates' then
            assert(type(r.active)=='boolean','gate active unavailable');out.active=r.active
            assert(not out.exit_sector.present or out.exit.present,'exit sector without exit')
        else
            assert(r.active==-1 and not out.exit.present and not out.exit_sector.present,'nongate exit/active values')
        end
        return out
    end
    local function validate(r)
        shape(r,fields);md_flags(r,{'stable'})
        assert(pending and r.kind=='SNAPSHOT' and r.provider=='x4-topology-observer-v1' and r.revision==revision
            and r.request==pending.id and r.session==stamp.session and r.generation==stamp.generation
            and r.world_token==stamp.world_token and r.query_kind==pending.kind and r.sector_macro==pending.sector,'snapshot binding')
        assert(finite(r.age_before) and finite(r.age_after) and r.age_after>=r.age_before
            and r.age_before>=last_native_age and r.stable==true,'snapshot identity/time changed')
        assert((r.total==-1 or integer(r.total,0,1000000)) and (r.scope_total==-1 or integer(r.scope_total,0,1000000))
            and integer(r.scope_matches,0,4096) and integer(r.unknown,0,4096),'snapshot denominators')
        list(r.rows,512)
        local scoped=r.query_kind=='zones' or r.query_kind=='gates'
        if r.status~='CAPTURED' then
            assert(r.status=='UNAVAILABLE' or r.status=='SCOPE_UNAVAILABLE' or r.status=='SCOPE_CAPACITY','unknown source status')
            assert(r.total==-1 and #r.rows==0,'unavailable census has rows')
            return nil,{status='UNAVAILABLE',request=r.request,reason=r.status,scope_total=r.scope_total,
                scope_matches=r.scope_matches,enumeration_complete=false,runtime_verified=false}
        end
        assert(r.total>=0 and #r.rows==math.min(r.total,512),'retained/total denominator mismatch')
        if scoped then assert(r.scope_total>=1 and r.scope_total<=4096 and r.scope_matches==1,'unqualified sector scope')
        else assert(r.scope_total==-1 and r.scope_matches==0,'unexpected sector scope') end
        local rows,seen,missing={}, {},0
        for _,raw in ipairs(r.rows) do
            local row=native_row(raw,r.query_kind)
            if row.exists then
                assert(not seen[row.id],'duplicate returned object');seen[row.id]=true
                if scoped then assert(row.sector.macro==r.sector_macro,'row escaped requested sector') end
            else missing=missing+1 end
            rows[#rows+1]=row
        end
        assert(r.unknown==missing,'unknown-row denominator mismatch')
        local complete=r.total==#rows and missing==0
        local meta={status=complete and 'CAPTURED' or 'PARTIAL',request=r.request,kind=r.query_kind,sector_macro=r.sector_macro,
            enumerated_total=r.total,retained=#rows,omitted=r.total-#rows,unreadable=missing,enumeration_complete=complete,
            scope='NORMAL_CLUSTER_NATIVE_FIND_DEFAULTS',zones_exclude_highways=true,gates_exclude_highway_endpoints=true,
            owner_filter=false,knowledge_filter=false,stamp=copy(stamp),age_before=r.age_before,age_after=r.age_after,
            age_domain='MD_PLAYER_AGE',atomic=false,runtime_verified=false,ids_are_observations_not_issued=true}
        assert(#encode(meta)<=3000,'metadata exceeds wire budget')
        return {rows=rows,meta=meta,received=now()},meta
    end
    local function drain()
        if retired or fault then return end
        local ok,why=pcall(function()
            current();local q=c.read();if q==nil then return end;list(q,64)
            local seen={}
            for _,r in ipairs(q) do
                assert(type(r)=='table' and hex(r.request,32) and used[r.request] and not seen[r.request],'foreign/duplicate batch')
                seen[r.request]=true
                assert(used[r.request]~='COMPLETE','duplicate completed reply')
            end
            c.clear()
            for _,r in ipairs(q) do
                if pending and r.request==pending.id then
                    assert(now()<=pending.deadline,'reply after deadline')
                    local capture,meta=validate(r);current()
                    last_native_age=r.age_after
                    saved=capture;result=meta;used[r.request]='COMPLETE';pending=nil
                end -- timed-out/superseded replies cannot become a result
            end
        end);if not ok then fail(why) end
    end
    local function tick()
        if retired or fault or not pending then return end
        local ok,why=pcall(function()
            current()
            if now()>pending.deadline then
                local id=pending.id;used[id]='TIMED_OUT';pending=nil;saved=nil
                result={status='UNAVAILABLE',request=id,reason='TIMEOUT',enumeration_complete=false,runtime_verified=false}
            end
        end);if not ok then fail(why) end
    end
    function api.begin(id,kind,sector)
        current();assert(hex(id,32) and not used[id] and count<256 and not pending,'fresh bounded topology request')
        assert(kinds[kind] and text(sector),'closed topology query')
        assert((kind=='zones' or kind=='gates')==(#sector>0),'explicit sector macro scope')
        if not started then
            started=true;stamp=current()
            local ok,why=pcall(function() c.arm_tick(tick);c.subscribe(drain) end)
            if not ok then fail(why);error(why) end
        end
        used[id]='PENDING';count=count+1;saved=nil;result=nil;pending={id=id,kind=kind,sector=sector,deadline=now()+10}
        current()
        local ok,why=pcall(c.send,'snapshot',{request=id,kind=kind,sector_macro=sector,
            session=stamp.session,generation=stamp.generation,world_token=stamp.world_token})
        if not ok then fail(why) end
        return fault and copy(result) or {status='PENDING',request=id}
    end
    function api.poll(id)
        assert(hex(id,32) and used[id],'unknown topology request')
        if fault then return copy(result) end
        current();tick()
        if result and result.request==id then return copy(result) end
        assert(pending and pending.id==id,'superseded topology request')
        return {status='PENDING',request=id}
    end
    function api.page(id,offset)
        current();assert(saved and saved.meta.request==id and now()-saved.received<=300,'snapshot absent/stale')
        local rows=saved.rows;assert(integer(offset,0,#rows),'page offset')
        local page={request=id,offset=offset,next_offset=offset,total_retained=#rows,rows={},
            enumeration_complete=saved.meta.enumeration_complete,end_of_retained=false}
        while page.next_offset<#rows and #page.rows<16 do
            page.rows[#page.rows+1]=rows[page.next_offset+1];page.next_offset=page.next_offset+1
            local ok,raw=pcall(encode,page)
            if not ok or #raw>3000 then table.remove(page.rows);page.next_offset=page.next_offset-1;break end
        end
        assert(page.next_offset>offset or offset==#rows,'one row exceeds page budget')
        page.end_of_retained=page.next_offset==#rows
        assert(#encode(page)<=3000,'page budget');current();return copy(page)
    end
    local disposal_failed=false
    function api.dispose()
        if disposal_failed then return false end
        if retired then return true end
        retired=true;pending=nil;saved=nil
        local ok,value=pcall(c.dispose)
        if not ok or value~=true then disposal_failed=true;return false end
        return true
    end
    return api
end

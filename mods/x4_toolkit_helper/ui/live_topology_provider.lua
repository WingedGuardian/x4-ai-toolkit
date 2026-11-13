-- Read-only topology SDK provider. Completed means one retained observation, never gameplay acceptance.
return function(d)
    local function hex(v,n) return type(v)=='string' and #v==n and not v:find('[^0-9a-f]') end
    assert(type(d)=='table' and hex(d.revision,64) and type(d.public_current)=='function'
        and type(d.encode)=='function' and type(d.observer)=='table','topology dependencies')
    local watch,revision,public,encode=d.observer,d.revision,d.public_current,d.encode
    for _,k in ipairs({'begin','poll','page','dispose'}) do assert(type(watch[k])=='function','observer method') end
    local api,records,count,stamp,retired,failed={}, {},0,nil,false,false
    local kinds={clusters=true,sectors=true,zones=true,gates=true}
    local function identity()
        assert(not retired,'topology provider retired')
        local s=public();assert(type(s)=='table' and s.provider=='x4-live-session-v1'
            and hex(s.revision,64) and hex(s.session,32) and hex(s.generation,32)
            and type(s.world_token)=='string' and #s.world_token>0 and #s.world_token<=512,'public identity')
        local copy={}
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do
            if stamp then assert(stamp[k]==s[k],'public identity changed') end;copy[k]=s[k]
        end
        stamp=copy;return s.session
    end
    local function args(req)
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'arguments')
        local allowed=req.action=='capture' and {kind=true,sector_macro=true} or {capture=true,offset=true}
        local n=0;for k in pairs(a) do assert(allowed[k],'unknown argument');n=n+1 end;assert(n==2,'argument count')
        if req.action=='capture' then
            assert(kinds[a.kind] and type(a.sector_macro)=='string' and #a.sector_macro<=512
                and not a.sector_macro:find('[%c]'),'topology scope')
            assert((a.kind=='zones' or a.kind=='gates')==(#a.sector_macro>0),'explicit sector scope')
            return {kind=a.kind,sector_macro=a.sector_macro}
        end
        assert(req.action=='page' and hex(a.capture,32) and type(a.offset)=='number'
            and a.offset>=0 and a.offset<=512 and a.offset==math.floor(a.offset),'page scope')
        return {capture=a.capture,offset=a.offset}
    end
    local function validate(req,r)
        assert(type(req)=='table' and req.provider=='x4-topology-observer-v1'
            and (req.action=='capture' or req.action=='page') and req.binding==revision
            and hex(req.id,32) and hex(req.digest,64) and req.session==identity(),'unbound request')
        local a=args(req)
        if r then
            assert(r.digest==req.digest and r.session==req.session and r.action==req.action,'request changed')
            for k,v in pairs(r.args) do assert(a[k]==v,'arguments changed') end
        end
        return a
    end
    local function reply(status,reason,value)
        local out={status=status,reason=reason,observations={read_only=true,gameplay_action_attempted=false,
            proof_scope='TOPOLOGY_RETAINED_OBSERVATION_ONLY',runtime_verified=false}}
        if value then
            local raw=encode(value);assert(type(raw)=='string' and #raw<=3000,'payload budget')
            out.observations.payload_json=raw
        end
        -- Reserve room for the SDK action_core fields and outer JSON quoting.
        assert(#encode({payload=encode(out),reserved=string.rep('x',900)})<=4000,'wrapped payload budget')
        return out
    end
    local function observed(id)
        local v=watch.poll(id);identity()
        assert(type(v)=='table' and v.request==id,'capture correlation')
        if v.status=='PENDING' then return reply('running','topology snapshot pending',v) end
        if v.status=='CAPTURED' or v.status=='PARTIAL' then
            assert(v.runtime_verified==false and type(v.enumeration_complete)=='boolean','observation scope')
            return reply('completed','metadata captured; retrieve every retained page separately',v)
        end
        assert(v.status=='UNAVAILABLE' or v.status=='UNQUALIFIED','unknown topology status')
        return reply('unknown','snapshot unavailable; no automatic retry',v)
    end
    local function compact(page)
        -- Fixed lossless tuple grammar; avoids repeating long keys in every row.
        local rows={}
        for _,v in ipairs(page.rows) do
            local row={v.exists,v.id or '',v.macro,v.active==nil and 'NOT_APPLICABLE' or v.active}
            for _,k in ipairs({'cluster','sector','zone','exit','exit_sector'}) do
                local x=v[k];row[#row+1]=(x and x.present) and {x.id,x.macro} or false
            end
            rows[#rows+1]=row
        end
        return {request=page.request,offset=page.offset,next_offset=page.next_offset,
            total_retained=page.total_retained,enumeration_complete=page.enumeration_complete,
            end_of_retained=page.end_of_retained,rows=rows,row_encoding='TOPOLOGY_TUPLE_V1'}
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','callback')
        local r=type(req)=='table' and records[req.id] or nil
        local ok,value=pcall(function()
            local a=validate(req,r)
            if phase=='prepare' then
                assert(not r and count<256,'duplicate/capacity')
                if req.action=='page' then
                    local origin=records[a.capture]
                    assert(origin and origin.action=='capture' and origin.dispatched,'capture not owned')
                end
                r={digest=req.digest,session=req.session,action=req.action,args=a,dispatched=false}
                records[req.id]=r;count=count+1
                return reply('prepared','read-only preparation; no native query sent')
            end
            assert(r,'private preparation missing')
            if phase=='execute' then
                assert(not r.dispatched,'already dispatched');r.dispatched=true
                if r.action=='capture' then
                    local started=watch.begin(req.id,r.args.kind,r.args.sector_macro);identity()
                    assert(type(started)=='table' and started.request==req.id,'dispatch correlation')
                    if started.status=='UNQUALIFIED' then return reply('unknown','snapshot dispatch unavailable',started) end
                    assert(started.status=='PENDING','dispatch status');return observed(req.id)
                end
                local v=watch.page(r.args.capture,r.args.offset);identity()
                assert(v.request==r.args.capture and v.offset==r.args.offset,'page correlation')
                v=compact(v)
                local packed
                while true do
                    local fits,value=pcall(reply,'completed','one retained page; no global completeness claim',v)
                    if fits then packed=value;break end
                    assert(#v.rows>1,'one row exceeds wrapped payload budget')
                    table.remove(v.rows);v.next_offset=v.next_offset-1;v.end_of_retained=false
                end
                r.payload=encode(v);assert(#r.payload<=3000,'page payload budget')
                return packed
            end
            assert(phase=='status','unknown phase')
            if not r.dispatched then return reply('prepared','private preparation retained') end
            if r.action=='capture' then return observed(req.id) end
            assert(r.payload,'page dispatch unqualified')
            return {status='completed',reason='historical immutable page',observations={read_only=true,
                gameplay_action_attempted=false,proof_scope='TOPOLOGY_RETAINED_OBSERVATION_ONLY',
                runtime_verified=false,payload_json=r.payload}}
        end)
        if not ok then value=reply(r and 'unknown' or 'refused',tostring(value)) end
        done(value)
    end
    function api.session() local ok,v=pcall(identity);return ok and v or nil end
    function api.dispose()
        if failed then return false end
        if retired then return true end
        retired=true;local ok,v=pcall(watch.dispose)
        if not ok or v~=true then failed=true;return false end
        return true
    end
    api.spec={id='x4-topology-observer-v1',revision=revision,actions={capture=true,page=true},
        session=api.session,invoke=invoke,dispose=api.dispose}
    return api
end

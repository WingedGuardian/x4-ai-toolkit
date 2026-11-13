-- Fixed native room mechanism provider. No opener, walking, docking or arrival proof.
return function(d)
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    assert(type(d)=='table' and type(d.adapter)=='table' and hex(d.revision,64)
        and type(d.public_current)=='function' and type(d.encode)=='function','room provider dependencies')
    local a,revision,current,encode=d.adapter,d.revision,d.public_current,d.encode
    for _,k in ipairs({'location','targets','prepare','execute','status','dispose'})do
        assert(type(a[k])=='function','room adapter method')
    end
    local api,records,stamp,count,capture,retired,failed={}, {},nil,0,nil,false,false
    local actions={location=true,targets=true,page=true,transport=true}
    local function clone(v)if type(v)~='table'then return v end;local r={}for k,x in pairs(v)do r[k]=clone(x)end;return r end
    local function identity()
        assert(not retired,'room provider retired');local s=current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=4096,'public identity')
        local out={}
        for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            if stamp then assert(stamp[k]==s[k],'public identity changed')end;out[k]=s[k]
        end
        stamp=out;return s.session
    end
    local function arguments(req)
        local v=req.arguments;assert(type(v)=='table' and getmetatable(v)==nil,'arguments')
        local fields=req.action=='page' and {capture=true,offset=true}
            or req.action=='transport' and {capture=true,index=true} or {}
        local n=0;for k in pairs(v)do assert(fields[k],'unexpected argument');n=n+1 end
        if req.action=='location' or req.action=='targets'then assert(n==0,'no arguments');return{}end
        assert(n==2 and hex(v.capture,32),'capture binding')
        local key=req.action=='page' and 'offset' or 'index';local x=v[key]
        assert(type(x)=='number' and x==math.floor(x) and x>=0 and x<=128
            and (key=='offset' or x>0),'row index')
        return clone(v)
    end
    local function validate(req,r)
        assert(type(req)=='table' and req.provider=='x4-rooms-v1' and actions[req.action]
            and req.binding==revision and hex(req.id,32) and hex(req.digest,64)
            and req.session==identity(),'unbound room request')
        local args=arguments(req)
        if r then
            assert(req.action==r.action and req.digest==r.digest and req.session==r.session,'request changed')
            for k,v in pairs(r.args)do assert(args[k]==v,'arguments changed')end
        end
        return args
    end
    local function reply(status,reason,value,possible)
        local out={status=status,reason=reason,observations={mechanism='NATIVE_TRANSPORTER_NOT_RENDERED_BUTTON',
            action_attempted_or_uncertain=possible==true,arrival_verified=false,walking_verified=false,
            docking_verified=false,menu_opening_supported=false,runtime_verified=false}}
        if value then
            out.observations.payload_json=encode(value)
            assert(#encode({payload=encode(out),reserve=string.rep('x',900)})<=4000,'wrapped room payload budget')
        end
        return out
    end
    local function native_result(v)
        assert(type(v)=='table','native result unavailable')
        if v.status=='TRANSPORT_REQUESTED'then
            assert(v.action_attempted==true and v.arrival_verified==false,'transport scope')
            return reply('running','native transport requested; arrival unverified',v,true)
        end
        assert(v.status=='REFUSED' or v.status=='UNKNOWN','unexpected transport result')
        return reply(v.status=='REFUSED' and 'refused' or 'unknown','native transport not completed',v,v.action_attempted)
    end
    local function invoke(phase,req,done)
        local r=type(req)=='table' and records[req.id] or nil
        local ok,value=pcall(function()
            local args=validate(req,r)
            if phase=='prepare'then
                assert(not r and count<256,'duplicate/capacity')
                r={action=req.action,args=args,digest=req.digest,session=req.session,sent=false}
                records[req.id]=r;count=count+1
                if req.action=='page' or req.action=='transport'then
                    assert(capture and capture.id==args.capture,'current owned capture required')
                    if req.action=='page'then assert(args.offset<=#capture.rows,'page offset')end
                end
                if req.action=='transport'then
                    local ticket,why=a.prepare(capture.native,args.index);identity()
                    assert(ticket,why or 'native preparation refused');r.ticket=ticket
                end
                return reply('prepared','private room preparation; no transport requested')
            end
            assert(r,'private preparation missing')
            if phase=='execute'then
                assert(not r.sent,'already dispatched');r.sent=true
                if r.action=='transport'then
                    assert(r.ticket,'native preparation unavailable')
                    r.may_have_acted=true
                    local v=a.execute(r.ticket);identity();r.native=clone(v)
                    return native_result(v)
                end
                if r.action=='page'then
                    assert(capture and capture.id==r.args.capture,'capture superseded')
                    local p={capture=capture.id,offset=r.args.offset,next_offset=r.args.offset,
                        total=capture.count,rows={},end_of_retained=false}
                    while p.next_offset<capture.count and #p.rows<16 do
                        p.rows[#p.rows+1]=clone(capture.rows[p.next_offset+1]);p.next_offset=p.next_offset+1
                        local fits=pcall(reply,'completed','one immutable room target page',p,false)
                        if not fits then table.remove(p.rows);p.next_offset=p.next_offset-1;break end
                    end
                    assert(p.next_offset>p.offset or p.offset==p.total,'one room row exceeds wire budget')
                    p.end_of_retained=p.next_offset==p.total
                    r.result=reply('completed','one immutable room target page',p,false);return r.result
                end
                local v=r.action=='location' and a.location() or a.targets();identity()
                assert(type(v)=='table','room observation unavailable')
                if v.status~='OBSERVED'then
                    assert(v.status=='UNAVAILABLE','unexpected room observation')
                    r.result=reply('unknown','native room observation unavailable',v,false);return r.result
                end
                local next_capture
                if r.action=='targets'then
                    assert(type(v.targets)=='table' and #v.targets==v.count and type(v.capture)=='string'
                        and v.complete_native_target_list==true,'native target denominator')
                    next_capture={id=req.id,native=v.capture,rows=clone(v.targets),count=v.count}
                    v=clone(v);v.targets=nil;v.capture=req.id;v.transport_pages_complete=false
                end
                r.result=reply('completed','sampled native observation only',v,false)
                if next_capture then capture=next_capture end
                return r.result
            end
            assert(phase=='status','unknown phase')
            if not r.sent then return reply('prepared','private preparation retained')end
            if r.action=='transport'then
                assert(r.ticket,'native preparation unavailable')
                local v=a.status(r.ticket);identity()
                assert(type(v)=='table' and v.historical==true and v.arrival_verified==false,'native status scope')
                assert(v.result,'native outcome unavailable')
                local out=native_result(v.result)
                out.observations.unresolved_transport=v.unresolved_transport
                out.observations.historical=true
                return out
            end
            assert(r.result,'observation not qualified')
            return clone(r.result)
        end)
        if not ok then value=reply(r and 'unknown' or 'refused',tostring(value):sub(1,512),nil,r and r.may_have_acted)end
        done(value)
    end
    function api.session()local ok,v=pcall(identity);return ok and v or nil end
    function api.dispose()
        if failed then return false end;if retired then return true end
        retired=true;local ok,v=pcall(a.dispose)
        if not ok or v~=true then failed=true;return false end;return true
    end
    api.spec={id='x4-rooms-v1',revision=revision,actions=actions,session=api.session,invoke=invoke,dispose=api.dispose}
    return api
end

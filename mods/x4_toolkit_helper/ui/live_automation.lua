-- Session-bound routing for the public pipe. Native adapters are injected.
-- No eval, file access, implicit handshake, retry or product-specific vocabulary.
return function(env)
    local unpack=table.unpack or unpack
    assert(type(env)=="table" and type(env.session)=="table")
    assert(type(env.wire)=="table" and type(env.registry)=="table")
    assert(type(env.read)=="function" and type(env.read_verbs)=="table")
    assert(type(env.age)=="function" and type(env.build)=="string")
    assert(type(env.ui_authorize)=="function","UI instance authority required")
    local profile=env.capacity_profile
    if profile==nil then profile="interactive" end
    assert(profile=="interactive" or profile=="campaign_v1","unknown fixed capacity profile")
    local limits
    if env.capacity_resolver~=nil then
        assert(type(env.capacity_resolver)=="function","trusted capacity resolver required")
        limits=env.capacity_resolver(profile)
        assert(type(limits)=="table" and getmetatable(limits)==nil and limits.name==profile
            and type(limits.revision)=="string" and #limits.revision==64
            and type(limits.source_sha256)=="string" and #limits.source_sha256==64
            and not limits.revision:find("[^0-9a-f]") and not limits.source_sha256:find("[^0-9a-f]"),"capacity source identity")
    else
        assert(profile=="interactive","campaign requires trusted generated capacity resolver")
        limits={name="interactive",revision="legacy-interactive",source_sha256="legacy-interactive",
            router_history=256,read_history=4096,router_retained_bytes=16777216,packet_bytes=1400}
    end
    local cap={}
    for _,k in ipairs({"name","revision","source_sha256","router_history","read_history","router_retained_bytes","packet_bytes"}) do cap[k]=limits[k] end
    for _,k in ipairs({"router_history","read_history","router_retained_bytes","packet_bytes"}) do
        assert(type(cap[k])=="number" and cap[k]>0 and cap[k]%1==0,"integer capacity required")
    end
    assert(cap.router_history==(profile=="interactive" and 256 or 8192)
        and cap.read_history==(profile=="interactive" and 4096 or 65536)
        and cap.router_retained_bytes==16777216 and cap.packet_bytes==1400,"capacity differs from reviewed profile")
    local api, pending, packets, packet_count = {}, false, {}, 0
    -- One maximum read payload slot plus ID/packet charges, retained for life.
    local retained_charge=32000
    local last_session,last_age=nil,nil
    local read_capture,read_ids,read_count=nil,{},0
    local function same(a,b)
        for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
            if a[k] ~= b[k] then return false end
        end
        return true
    end
    local function token(x)
        return type(x)=="string" and #x==32 and not x:find("[^0-9a-f]")
    end
    local function current(session,generation)
        local stamp,why=env.session.current()
        assert(type(stamp)=="table", why or "public session unavailable")
        assert(stamp.session==session and stamp.generation==generation,"stale session or UI generation")
        return stamp
    end
    local function encode(value)
        return env.wire.encode(value)
    end
    local function read_header(r,kind)
        return {version=1,kind=kind,capture=r.id,source_verb=r.verb,source_status=r.status,
                bytes=#r.payload,pages=math.max(1,math.ceil(#r.payload/1000)),chunk_bytes=1000,
                started_age=r.started,finished_age=r.finished,atomic=false}
    end
    function api.connect(args,send)
        assert(type(args)=="table" and #args==5 and type(send)=="function","handshake shape")
        local proof,mode,session,challenge,generation=unpack(args)
        assert(mode=="probe" or mode=="fresh" or mode=="resume","handshake mode")
        assert((mode=="probe" and session=="") or (mode~="probe" and token(session)),"handshake nonce")
        assert(token(challenge) and token(generation),"handshake correlation")
        assert(not pending,"handshake already pending")
        assert(env.ui_authorize(proof,generation)==true,"UI instance ticket refused")
        pending=true
        local sent=false
        local function finish(value)
            if sent then return end
            sent=true; pending=false
            local ok,payload=pcall(encode,value)
            if ok then send("OK",payload) else send("ERR","session reply encoding refused") end
        end
        local ok,why=pcall(env.session.connect,
            {mode=mode,session=session,challenge=challenge,generation=generation},finish)
        if not ok then
            if not sent then sent=true;pending=false;send("ERR","session connect failed: "..tostring(why)) end
        end
    end
    function api.bound(args)
        assert(type(args)=="table" and #args>=6 and #args<=32,"bound argument count")
        local session,generation,revision,world,build,verb=unpack(args,1,6)
        assert(token(session) and token(generation) and type(verb)=="string","bound identity")
        assert(not pending,"handshake pending")
        local stamp=current(session,generation)
        assert(stamp.provider=="x4-live-session-v1" and stamp.revision==revision
               and stamp.world_token==world and env.build==build,"unexpected public stamp or helper build")
        local started=env.age()
        assert(type(started)=="number" and started==started and started>=0 and started<math.huge,"native age unavailable")
        assert(last_session~=session or last_age==nil or started>=last_age,"native age rolled back before command")
        last_session,last_age=session,started
        local minimum_end=started
        local status,payload
        if verb=="read_capture" then
            local id,target=args[7],args[8]
            assert(token(id) and not read_ids[id] and read_count<cap.read_history,"fresh bounded read capture required")
            assert(type(target)=="string" and env.read_verbs[target]==true,"not an allowed capture read")
            assert(retained_charge+64<=cap.router_retained_bytes,"read identity history full")
            retained_charge=retained_charge+64
            read_count=read_count+1;read_ids[id]=true;read_capture=nil
            local rest={};for i=9,#args do rest[#rest+1]=args[i] end
            local source_status,source_payload=env.read(target,rest)
            assert(source_status=="OK" or source_status=="ABSENT" or source_status=="ERR","invalid source status")
            assert(type(source_payload)=="string" and #source_payload<=32000,"source read byte bound")
            local finished=env.age()
            assert(type(finished)=="number" and finished==finished and finished>=started and finished<math.huge,"source read clock unavailable")
            minimum_end=finished
            read_capture={id=id,verb=target,status=source_status,payload=source_payload,
                          started=started,finished=finished,stamp=stamp}
            status,payload="OK",encode(read_header(read_capture,"READ_CAPTURE"))
        elseif verb=="read_page" then
            assert(#args==8 and token(args[7]) and type(args[8])=="string"
                   and args[8]:match("^%d+$") and #args[8]<=2,"read page arguments")
            local r=read_capture
            assert(r and r.id==args[7] and same(r.stamp,stamp),"read capture absent or superseded")
            assert(started>=r.finished and started-r.finished<=300,"read capture expired or clock changed")
            local page=tonumber(args[8]);local out=read_header(r,"READ_PAGE")
            assert(page>=0 and page<out.pages,"read page index")
            local bytes=r.payload:sub(page*1000+1,page*1000+1000)
            out.index=page;out.offset=page*1000;out.length=#bytes
            out.hex=bytes:gsub(".",function(c) return string.format("%02x",c:byte()) end)
            status,payload="OK",encode(out)
        elseif verb=="action_prepare" or verb=="action_execute" or verb=="action_status" then
            assert(#args==7,"action accepts exactly one packet")
            local req=env.wire.decode(args[7])
            assert(req.session==session,"action belongs to another session")
            if packets[req.id] then assert(packets[req.id]==args[7],"changed prepared packet")
            else
                assert(verb=="action_prepare","unknown prepared packet")
                assert(packet_count<cap.router_history,"packet history full")
                assert(type(args[7])=="string" and #args[7]<=cap.packet_bytes,"packet byte bound")
                assert(retained_charge+#args[7]+64<=cap.router_retained_bytes,
                    "packet retention full; refuse before registry invocation")
                retained_charge=retained_charge+#args[7]+64
                packets[req.id]=args[7];packet_count=packet_count+1
            end
            local result
            if verb=="action_prepare" then result=env.registry.prepare(req)
            elseif verb=="action_execute" then result=env.registry.execute(req.id,req.session,req.digest)
            else result=env.registry.status(req.id,req.session,req.digest) end
            status,payload="OK",encode(result)
        else
            assert(env.read_verbs[verb]==true,"not an allowed bound read")
            local rest={}
            for i=7,#args do rest[#rest+1]=args[i] end
            status,payload=env.read(verb,rest)
        end
        assert(status=="OK" or status=="ABSENT" or status=="ERR","invalid inner status")
        assert(type(payload)=="string","invalid inner payload")
        local after=current(session,generation)
        assert(same(stamp,after),"identity changed during command; outcome unqualified")
        local age=env.age()
        assert(type(age)=="number" and age==age and age>=0 and age<math.huge,"native age unavailable")
        assert(age>=minimum_end,"native age rolled back during command")
        last_age=age
        return encode({version=1,stamp=after,build=env.build,age=age,status=status,payload=payload})
    end
    function api.capacity()
        return {profile=cap.name,revision=cap.revision,source_sha256=cap.source_sha256,
            scope="RETAINED_VALUE_CHARGES_NOT_RSS",requests_used=packet_count,requests_limit=cap.router_history,
            reads_used=read_count,reads_limit=cap.read_history,retained_charge_bytes=retained_charge,
            retained_charge_limit_bytes=cap.router_retained_bytes,read_payload_reserved_bytes=32000,eviction=false}
    end
    function api.preflight(new_requests,new_reads)
        for _,v in ipairs({new_requests,new_reads}) do
            assert(type(v)=="number" and v>=0 and v%1==0 and v<=65536,"bounded proposed count required")
        end
        local out=api.capacity()
        out.requested=new_requests;out.reads_requested=new_reads
        out.worst_case_new_charge_bytes=new_requests*(cap.packet_bytes+64)+new_reads*64
        out.fits=packet_count+new_requests<=cap.router_history and read_count+new_reads<=cap.read_history
            and retained_charge+out.worst_case_new_charge_bytes<=cap.router_retained_bytes
        out.reserved=false
        return out
    end
    return api
end


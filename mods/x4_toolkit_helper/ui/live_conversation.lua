-- Fixed read-only MD snapshot adapter. No native dialogue selection or generic MD execution.
-- begin is observational request bookkeeping; poll never reissues the request.
return function(d)
    assert(type(d)=="table" and type(d.channel)=="table" and type(d.revision)=="string"
        and #d.revision==64 and not d.revision:find("[^0-9a-f]"),"fixed observer dependencies required")
    local channel,revision=d.channel,d.revision
    for _,k in ipairs({"public_current","now","subscribe","arm_tick","send","read","clear","component_id","dispose"}) do
        assert(type(channel[k])=="function","missing channel method: "..k)
    end
    local fields={"kind","provider","revision","request","session","generation","world_token","age_before","age_after",
        "sequence","instance","history_session","history_conversation","event_age","event_kind","section","history_matches",
        "stable","active_before","active_after","conversation_before","conversation_after","live_kind","live_component",
        "live_context","live_seed","history_kind","history_component","history_context","history_seed"}
    local allowed={};for _,k in ipairs(fields) do allowed[k]=true end
    local retired,fault,initialized=false,nil,false
    local pending,result,stamp,last_now=nil,nil,nil,nil
    local last_sequence,last_instance,last_native_age=0,0,0
    local used,count={},0
    local api={}
    local function copy(t)
        local out={};for k,v in pairs(t) do out[k]=type(v)=="table" and copy(v) or v end;return out
    end
    local function token(x) return type(x)=="string" and #x==32 and not x:find("[^0-9a-f]") end
    local function number(x) return type(x)=="number" and x==x and x>=0 and x<math.huge end
    local function integer(x) return number(x) and x%1==0 and x<=1000000 end
    local function text(x) return type(x)=="string" and #x<=512 end
    local function now()
        local v=channel.now();assert(number(v) and (not last_now or v>=last_now),"observer UI clock unavailable")
        last_now=v;return v
    end
    local function current()
        assert(not retired and not fault,"observer retired or faulted")
        local s=channel.public_current()
        assert(type(s)=="table" and s.provider=="x4-live-session-v1" and token(s.session) and token(s.generation)
            and type(s.revision)=="string" and #s.revision==64 and not s.revision:find("[^0-9a-f]")
            and text(s.world_token) and #s.world_token>0,"canonical observer session unavailable")
        local out={}
        for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
            if stamp then assert(stamp[k]==s[k],"observer public identity changed") end
            out[k]=s[k]
        end
        return out
    end
    local function fail(why)
        fault=tostring(why);pending=nil
        result={status="UNQUALIFIED",scope="READ_ONLY_CONVERSATION_EVENT_HISTORY",reason=fault,
            rendered_route="BLOCKED",runtime_verified=false}
    end
    local function component(value)
        -- In particular, never send the MD [context,template] table to a native converter.
        local kind=type(value)
        assert(kind=="number" or kind=="cdata" or kind=="string","component observation is not scalar")
        if kind=="number" then assert(integer(value) or (number(value) and value%1==0 and value<9007199254740992),"unsafe component number") end
        if kind=="string" then assert(#value>0 and #value<=64 and value:match("^%d+ULL$") or
            (#value>0 and #value<=64 and value:match("^%d+$")),"invalid component string") end
        assert(value~=0 and value~="0" and value~="0ULL","null component")
        local id=channel.component_id(value)
        assert(type(id)=="string" and #id<=64 and (id:match("^[1-9]%d*ULL$") or id:match("^[1-9]%d*$")),"component identity unavailable")
        return id
    end
    local function actor(r,prefix)
        local kind=r[prefix.."kind"]
        if kind=="COMPONENT" then
            assert(r[prefix.."context"]==0 and r[prefix.."seed"]=="UNKNOWN","mixed component actor")
            return {kind=kind,component=component(r[prefix.."component"])}
        elseif kind=="TEMPLATE" then
            local seed=r[prefix.."seed"]
            assert(r[prefix.."component"]==0 and type(seed)=="string" and #seed>0 and #seed<=32
                and seed:match("^%d+$"),"invalid template actor")
            return {kind=kind,context=component(r[prefix.."context"]),seed=seed}
        end
        assert(kind=="UNKNOWN" and r[prefix.."component"]==0 and r[prefix.."context"]==0
            and r[prefix.."seed"]=="UNKNOWN","malformed unknown actor")
        return {kind="UNKNOWN"}
    end
    local function equal(a,b)
        if a.kind~=b.kind or a.kind=="UNKNOWN" then return false end
        return a.kind=="COMPONENT" and a.component==b.component or
            a.kind=="TEMPLATE" and a.context==b.context and a.seed==b.seed
    end
    local function validate(r)
        assert(type(r)=="table" and getmetatable(r)==nil,"plain snapshot required")
        local n=0;for k in pairs(r) do assert(allowed[k],"unknown snapshot field");n=n+1 end
        assert(n==#fields,"missing snapshot fields")
        assert(r.kind=="SNAPSHOT" and r.provider=="x4-conversation-observer-v1" and r.revision==revision,"wrong observer producer")
        assert(pending and r.request==pending.id and r.session==stamp.session and r.generation==stamp.generation
            and r.world_token==stamp.world_token,"wrong snapshot correlation")
        for _,k in ipairs({"history_matches","stable","active_before","active_after"}) do assert(type(r[k])=="boolean","invalid boolean") end
        for _,k in ipairs({"history_session","history_conversation","event_kind","section","conversation_before","conversation_after"}) do assert(text(r[k]),"invalid text") end
        assert(r.history_session=="" or token(r.history_session),"bad historical session")
        assert(integer(r.sequence) and integer(r.instance) and r.instance<=r.sequence,"bad event counters")
        assert(r.sequence>=last_sequence and r.instance>=last_instance,"event counters rolled back")
        assert(r.event_kind=="INVALIDATED" or r.event_kind=="STARTED" or r.event_kind=="SECTION"
            or r.event_kind=="RETURNED" or r.event_kind=="FINISHED","unknown event kind")
        assert(number(r.age_before) and number(r.age_after) and r.age_after>=r.age_before
            and r.age_before>=last_native_age and number(r.event_age) and r.event_age<=r.age_after,"bad native chronology")
        local live,history=actor(r,"live_"),actor(r,"history_")
        local stable=r.stable and r.active_before==r.active_after and r.conversation_before==r.conversation_after
        local joined=stable and r.history_matches and r.active_after and r.instance>0
            and r.history_session==stamp.session and r.history_conversation==r.conversation_after
            and #r.history_conversation>0 and equal(live,history)
        assert(not r.history_matches or joined,"contradictory history match")
        local status=not stable and "UNQUALIFIED" or not r.active_after and "INACTIVE_OBSERVED"
            or live.kind=="UNKNOWN" and "UNQUALIFIED" or "ACTOR_OBSERVED_SECTION_UNKNOWN"
        local section_scope="UNKNOWN"
        if joined and #r.section>0 and (r.event_kind=="SECTION" or r.event_kind=="RETURNED") then
            status="ACTOR_AND_HISTORICAL_SECTION_OBSERVED";section_scope="EVENT_HISTORY_SAME_OBSERVED_INSTANCE"
        end
        last_sequence,last_instance,last_native_age=r.sequence,r.instance,r.age_after
        return {status=status,scope="READ_ONLY_CONVERSATION_EVENT_HISTORY",request=r.request,stamp=copy(stamp),
            actor=live,history_actor=history,sequence=r.sequence,instance=r.instance,event_kind=r.event_kind,
            conversation=r.conversation_after,section=r.section,section_scope=section_scope,
            history_conversation=r.history_conversation,history_session=r.history_session,
            history_matches=joined,active=r.active_after,stable=stable,age_before=r.age_before,age_after=r.age_after,
            event_age=r.event_age,rendered_route="BLOCKED",rendered_visibility="UNKNOWN",atomic=false,runtime_verified=false}
    end
    local function drain()
        if retired or fault then return end
        local ok,why=pcall(function()
            current();local rows=channel.read()
            if rows==nil then return end
            assert(type(rows)=="table" and getmetatable(rows)==nil,"snapshot queue malformed")
            local total=0;for k in pairs(rows) do assert(type(k)=="number" and k%1==0 and k>=1 and k<=64,"snapshot queue key");total=total+1 end
            assert(total==#rows,"sparse snapshot queue")
            channel.clear()
            for _,r in ipairs(rows) do
                assert(type(r)=="table" and token(r.request) and used[r.request],"unknown snapshot request")
                if pending and r.request==pending.id then
                    assert(now()<=pending.deadline,"snapshot arrived after deadline")
                    local value=validate(r);current();result=value;pending=nil;used[r.request]="COMPLETE"
                elseif used[r.request]=="COMPLETE" and result and result.request==r.request then
                    error("duplicate completed snapshot")
                end -- known superseded/time-out replies never acquire current authority
            end
        end)
        if not ok then fail(why) end
    end
    local function tick()
        if retired or fault or not pending then return end
        local ok,why=pcall(function()
            current()
            if now()>pending.deadline then
                local id=pending.id;used[id]="TIMED_OUT";pending=nil
                result={status="UNAVAILABLE",request=id,scope="READ_ONLY_CONVERSATION_EVENT_HISTORY",
                    reason="SNAPSHOT_TIMEOUT",rendered_route="BLOCKED",runtime_verified=false}
            end
        end)
        if not ok then fail(why) end
    end
    function api.begin(id)
        local s=current();assert(token(id) and not used[id] and count<256,"fresh bounded observer request required")
        assert(not pending,"snapshot already pending")
        if not initialized then
            initialized=true;stamp=s
            local ok,why=pcall(function() channel.arm_tick(tick);channel.subscribe(drain) end)
            if not ok then fail(why);error(why) end
        end
        local started=now();used[id]="PENDING";count=count+1;result=nil
        pending={id=id,deadline=started+10}
        local ok,why=pcall(channel.send,"snapshot",{request=id,session=stamp.session,generation=stamp.generation,world_token=stamp.world_token})
        if not ok then fail(why) end
        return fault and copy(result) or {status="PENDING",request=id,scope="READ_ONLY_CONVERSATION_EVENT_HISTORY"}
    end
    function api.poll(id)
        assert(token(id) and used[id],"unknown observer request")
        if fault then return copy(result) end
        current();tick()
        if result and (result.request==id or fault) then return copy(result) end
        assert(pending and pending.id==id,"superseded observer request")
        return {status="PENDING",request=id,scope="READ_ONLY_CONVERSATION_EVENT_HISTORY"}
    end
    local disposal_failed=false
    function api.dispose()
        if disposal_failed then return false end
        if retired then return true end
        retired=true;pending=nil
        local ok,value=pcall(channel.dispose)
        if not ok or value~=true then disposal_failed=true;return false end
        return true
    end
    return api
end

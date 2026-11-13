-- Observation-only SDK provider. Completion qualifies a captured snapshot, never
-- conversation success, rendered menu visibility or a gameplay transaction.
return function(d)
    local function hex(x,n) return type(x)=="string" and #x==n and not x:find("[^0-9a-f]") end
    assert(type(d)=="table" and hex(d.revision,64) and type(d.public_current)=="function"
        and type(d.observer)=="table","fixed observation provider dependencies required")
    local observer,revision,public=d.observer,d.revision,d.public_current
    for _,k in ipairs({"begin","poll","dispose"}) do assert(type(observer[k])=="function","missing observer method") end
    local api,records,count,stamp,retired={}, {},0,nil,false
    local function identity()
        assert(not retired,"conversation provider retired")
        local s=public()
        assert(type(s)=="table" and s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string"
            and #s.world_token>0 and #s.world_token<=512,"public session unavailable")
        local copy={}
        for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
            if stamp then assert(stamp[k]==s[k],"public session drift") end;copy[k]=s[k]
        end
        stamp=copy;return s.session
    end
    local function validate(req)
        assert(type(req)=="table" and req.provider=="x4-conversation-observer-v1" and req.action=="snapshot"
            and req.binding==revision and hex(req.id,32) and hex(req.digest,64) and req.session==identity(),
            "unbound observer request")
        assert(type(req.arguments)=="table" and getmetatable(req.arguments)==nil and next(req.arguments)==nil,
            "snapshot takes no arguments")
    end
    local function reply(state,reason,value)
        return {status=state,reason=reason,observations={read_only=true,gameplay_action_attempted=false,
            proof_scope="CONVERSATION_SNAPSHOT_ONLY",snapshot=value or {status="NOT_CAPTURED"},
            rendered_route="BLOCKED",runtime_verified=false}}
    end
    local function observed(id)
        local value=observer.poll(id);identity()
        assert(type(value)=="table" and type(value.status)=="string","malformed observer result")
        if value.status=="PENDING" then return reply("running","fixed snapshot pending",value) end
        if value.status=="INACTIVE_OBSERVED" or value.status=="ACTOR_OBSERVED_SECTION_UNKNOWN"
            or value.status=="ACTOR_AND_HISTORICAL_SECTION_OBSERVED" then
            assert(value.request==id and value.rendered_route=="BLOCKED" and value.runtime_verified==false,
                "observer scope/correlation mismatch")
            return reply("completed","snapshot captured; no conversation/UI acceptance",value)
        end
        assert(value.status=="UNAVAILABLE" or value.status=="UNQUALIFIED","unknown observer status")
        return reply("unknown","snapshot unqualified; no automatic retry",value)
    end
    local function invoke(phase,req,done)
        assert(type(done)=="function","callback required")
        local r=type(req)=="table" and records[req.id] or nil
        local ok,value=pcall(function()
            validate(req)
            if r then assert(r.digest==req.digest and r.session==req.session,"changed observer request") end
            if phase=="prepare" then
                assert(not r and count<256,"duplicate preparation or observer capacity")
                r={digest=req.digest,session=req.session,dispatched=false};records[req.id]=r;count=count+1
                return reply("prepared","read-only snapshot ready; no request sent")
            end
            assert(r,"unknown private preparation")
            if phase=="execute" then
                assert(not r.dispatched,"snapshot already requested")
                r.dispatched=true -- reserve before send, including uncertain transport failure
                local started=observer.begin(req.id);identity()
                assert(type(started)=="table" and type(started.status)=="string","malformed snapshot dispatch")
                if started.status=="UNQUALIFIED" then return reply("unknown","snapshot dispatch unqualified",started) end
                assert(started.status=="PENDING" and started.request==req.id,"unexpected snapshot dispatch")
                return observed(req.id)
            end
            assert(phase=="status","unknown observation phase")
            if not r.dispatched then return reply("prepared","private snapshot preparation retained") end
            return observed(req.id)
        end)
        if not ok then value=reply(r and "unknown" or "refused",tostring(value)) end
        done(value)
    end
    function api.session() local ok,value=pcall(identity);return ok and value or nil end
    local disposal_failed=false
    function api.dispose()
        if disposal_failed then return false end
        if retired then return true end
        retired=true
        local ok,result=pcall(observer.dispose)
        if not ok or result~=true then disposal_failed=true;return false end
        return true
    end
    api.spec={id="x4-conversation-observer-v1",revision=revision,actions={snapshot=true},
        session=api.session,invoke=invoke,dispose=api.dispose}
    return api
end

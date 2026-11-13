-- Registered action lifecycle core. No native calls, registration side effects,
-- transport, arbitrary evaluation or automatic retries. Installed adapters own
-- native eligibility and independent readback; this module owns request identity.
-- MD transport analogue: ModSupportAPI named_pipes.xml access IDs/completion events.
-- Its I/O retry behavior is intentionally NOT inherited for game mutations.
return function(capacity_profile, capacity_resolver)
    local profile=capacity_profile
    if profile==nil then profile="interactive" end
    assert(profile=="interactive" or profile=="campaign_v1","unknown fixed capacity profile")
    local limits
    if capacity_resolver~=nil then
        assert(type(capacity_resolver)=="function","trusted capacity resolver required")
        limits=capacity_resolver(profile)
        assert(type(limits)=="table" and getmetatable(limits)==nil and limits.name==profile
            and type(limits.revision)=="string" and #limits.revision==64
            and type(limits.source_sha256)=="string" and #limits.source_sha256==64
            and not limits.revision:find("[^0-9a-f]") and not limits.source_sha256:find("[^0-9a-f]"),"capacity source identity")
    else
        assert(profile=="interactive","campaign requires trusted generated capacity resolver")
        limits={name="interactive",revision="legacy-interactive",source_sha256="legacy-interactive",
            sdk_history=256,sdk_retained_bytes=67108864,sdk_result_bytes=131072,
            sdk_request_bytes=24576,sdk_record_overhead=1024}
    end
    -- Copy only consumed scalar limits; external table mutation cannot widen them.
    local cap={}
    for _,k in ipairs({"name","revision","source_sha256","sdk_history","sdk_retained_bytes",
        "sdk_result_bytes","sdk_request_bytes","sdk_record_overhead"}) do cap[k]=limits[k] end
    for _,k in ipairs({"sdk_history","sdk_retained_bytes","sdk_result_bytes","sdk_request_bytes","sdk_record_overhead"}) do
        assert(type(cap[k])=="number" and cap[k]>0 and cap[k]%1==0,"integer capacity required")
    end
    assert(cap.sdk_history==(profile=="interactive" and 256 or 8192)
        and cap.sdk_retained_bytes==67108864 and cap.sdk_result_bytes==(profile=="interactive" and 131072 or 16384)
        and cap.sdk_request_bytes==(profile=="interactive" and 24576 or 4096)
        and cap.sdk_record_overhead==1024,"capacity exceeds reviewed profile")
    local providers, requests, count = {}, {}, 0
    local retained_charge=0
    local api = {}
    local terminal = {completed=true, refused=true, failed=true}
    local function token(v)
        return type(v) == "string" and #v > 0 and #v <= 128
            and not v:find("[^%w_.:-]")
    end
    local function hex(v, n)
        return type(v) == "string" and #v == n and not v:find("[^0-9a-f]")
    end
    local function clone(v, depth, budget)
        depth, budget = depth or 0, budget or {n=0,bytes=0,max=65536}
        budget.n = budget.n + 1
        budget.bytes = budget.bytes + 8 + (type(v) == "string" and #v or 0)
        assert(depth <= 6 and budget.n <= 512, "bounded JSON value required")
        assert(budget.bytes <= budget.max, "value byte budget exceeded")
        local typ = type(v)
        if typ == "nil" or typ == "boolean" then return v end
        if typ == "number" then
            assert(v == v and v > -math.huge and v < math.huge, "finite value required")
            return v
        end
        if typ == "string" then assert(#v <= 4096, "string limit"); return v end
        assert(typ == "table", "plain JSON values required")
        assert(getmetatable(v) == nil, "metatable not allowed")
        local result = {}
        for k, x in pairs(v) do
            assert((type(k) == "string" and #k <= 128) or
                (type(k) == "number" and k >= 1 and k % 1 == 0 and k <= 512),
                "invalid JSON key")
            result[k] = clone(x, depth + 1, budget)
        end
        return result
    end
    local function same(a, b)
        if type(a) ~= type(b) then return false end
        if type(a) ~= "table" then return a == b end
        for k, v in pairs(a) do if not same(v, b[k]) then return false end end
        for k in pairs(b) do if a[k] == nil then return false end end
        return true
    end
    -- Logical retained-value charge includes keys and per-value overhead. It is
    -- deliberately not a Lua allocator/RSS measurement.
    local function charge(v)
        local n=8
        if type(v)=="string" then return n+#v end
        if type(v)=="table" then
            for k,x in pairs(v) do n=n+(type(k)=="string" and #k or 8)+charge(x) end
        end
        return n
    end
    local function settle_reservation(r,value_charge)
        if terminal[r.state] and not r.sealed then
            local actual=r.request_charge+value_charge+cap.sdk_record_overhead
            assert(actual<=r.charge,"result exceeded its pre-effect reservation")
            retained_charge=retained_charge-r.charge+actual
            r.charge=actual;r.sealed=true
        end
    end
    local function request(v)
        assert(type(v) == "table" and getmetatable(v) == nil, "request table required")
        local allowed = {version=true,id=true,session=true,provider=true,action=true,
            arguments=true,binding=true,digest=true}
        local n = 0
        for k in pairs(v) do assert(allowed[k], "unexpected request field"); n=n+1 end
        assert(n == 8 and v.version == 1 and hex(v.id,32) and hex(v.digest,64),
            "version/id/digest required")
        for _, k in ipairs({"session","provider","action","binding"}) do
            assert(token(v[k]), "invalid request token")
        end
        assert(type(v.arguments) == "table", "argument object required")
        return clone(v, 0, {n=0,bytes=0,max=1400})
    end
    local function snapshot(r)
        local observations = clone(r.observations)
        observations.action_core = {protocol_fault=r.fault or false,
            superseded_status_replies=r.superseded or 0,
            superseded_prepare_replies=r.superseded_prepare or 0,
            provider_revision=r.provider.revision}
        return {version=1,id=r.request.id,session=r.request.session,
            digest=r.request.digest,status=r.state,observations=observations,
            reason=r.reason}
    end
    local function uncertain(r, why, fault)
        if r.fault then return end
        r.state, r.reason = "unknown", why
        if fault then r.fault = true end
    end
    local function current(r)
        local ok, session = pcall(r.provider.session)
        if not ok or not token(session) or session ~= r.request.session then
            uncertain(r, "provider session unavailable or changed", true)
            return false
        end
        return true
    end
    local function invoke(r, phase, generation)
        local delivered = false
        local preparation = phase == "prepare" or (phase == "status" and not r.consumed)
        local function done(result)
            if phase == "prepare" and generation ~= r.prepare_generation then
                r.superseded_prepare = (r.superseded_prepare or 0) + 1
                return
            end
            if phase == "status" and generation ~= r.status_generation then
                r.superseded = (r.superseded or 0) + 1
                return
            end
            if delivered then uncertain(r, "adapter callback delivered twice", true); return end
            delivered = true
            r.pending[phase] = nil
            if r.fault then return end
            if not current(r) then return end
            local ok, value = pcall(clone, result)
            if not ok or type(value) ~= "table" or type(value.reason) ~= "string"
                or type(value.observations) ~= "table" then
                uncertain(r, "malformed adapter result", true); return
            end
            local value_charge=charge(value)
            if value_charge>cap.sdk_result_bytes then
                uncertain(r,"adapter result exceeds reserved profile value bound",true);return
            end
            local state = value.status
            local allowed = preparation and
                (state == "prepared" or state == "refused" or state == "unknown") or
                not preparation and
                (state == "running" or state == "completed" or state == "refused"
                    or state == "failed" or state == "unknown")
            if not allowed then uncertain(r, "invalid adapter outcome", true); return end
            if terminal[r.state] then
                uncertain(r, "late result after terminal outcome", true); return
            end
            r.state, r.reason, r.observations = state, value.reason, value.observations
            settle_reservation(r,value_charge)
        end
        r.pending[phase] = true
        local ok = pcall(r.provider.invoke, phase, clone(r.request), done)
        if not ok then
            -- Even an exception before callback may follow a native write.
            uncertain(r, "adapter invocation raised; no retry", delivered)
            r.pending[phase] = nil
        end
    end
    function api.register(spec)
        assert(type(spec) == "table" and token(spec.id) and hex(spec.revision,64),
            "provider identity and source revision required")
        assert(not providers[spec.id], "provider already registered")
        assert(type(spec.session) == "function" and type(spec.invoke) == "function",
            "provider callbacks required")
        assert(type(spec.actions) == "table", "closed action vocabulary required")
        local actions, n = {}, 0
        for action, enabled in pairs(spec.actions) do
            assert(token(action) and enabled == true, "invalid action registration")
            actions[action], n = true, n+1
        end
        assert(n > 0 and n <= 128, "provider action count outside bound")
        providers[spec.id] = {id=spec.id,revision=spec.revision,session=spec.session,
            invoke=spec.invoke,actions=actions}
    end
    function api.prepare(value)
        local req = request(value)
        local existing = requests[req.id]
        if existing then
            assert(same(existing.request, req), "request id reused with changed content")
            current(existing)
            return snapshot(existing)
        end
        assert(count < cap.sdk_history, "request history full; no eviction or automatic replay")
        local provider = providers[req.provider]
        assert(provider and provider.actions[req.action], "unregistered provider/action")
        local request_charge=charge(req)
        assert(request_charge<=cap.sdk_request_bytes,"request exceeds retained profile value bound")
        local reservation=request_charge+cap.sdk_result_bytes+cap.sdk_record_overhead
        assert(retained_charge+reservation<=cap.sdk_retained_bytes,
            "request result reservation full; refuse before adapter invocation")
        local r = {request=req,provider=provider,state="preparing",reason="preparation pending",
            observations={},consumed=false,pending={},prepare_generation=1,
            request_charge=request_charge,charge=reservation,sealed=false}
        retained_charge=retained_charge+reservation
        requests[req.id], count = r, count+1
        if current(r) then invoke(r, "prepare", r.prepare_generation) end
        return snapshot(r)
    end
    local function bound(id, session, digest)
        assert(hex(id,32) and token(session) and hex(digest,64), "bound request required")
        local r = requests[id]
        assert(r and r.request.session == session and r.request.digest == digest,
            "unknown or mismatched request")
        return r
    end
    function api.execute(id, session, digest)
        local r = bound(id,session,digest)
        if r.fault or not current(r) then return snapshot(r) end
        if r.consumed or r.state ~= "prepared" or r.pending.prepare or r.pending.status then
            return snapshot(r)
        end
        -- Consume BEFORE calling installed adapter code. Never retry this request.
        r.consumed, r.state, r.reason = true, "running", "dispatch started; outcome pending"
        invoke(r, "execute")
        return snapshot(r)
    end
    function api.status(id, session, digest)
        local r = bound(id,session,digest)
        -- run-12 (ROR campaign): with an ASYNCHRONOUS provider (an MD-backed prepare) the ack lands after prepare
        -- returned "preparing", and the only way the host can observe it is status(). Resetting an already
        -- "prepared" request here discarded that ack every time, so it could never be executed. An arrived ack
        -- (prepare or an earlier readiness reply) IS the answer: report it, invoke nothing.
        if not r.fault and current(r) and not r.consumed and r.state == "prepared"
                and not r.pending.prepare and not r.pending.status then
            return snapshot(r)
        end
        if not r.fault and current(r) and not terminal[r.state] then
            -- Status is read-only. A NEW explicit request supersedes a lost status
            -- callback; it never repeats execute. Old responses cannot replace it.
            r.status_generation = (r.status_generation or 0) + 1
            if not r.consumed then
                -- Read-only readiness replaces a missing preparation ack; never
                -- repeat prepare or let the superseded ack restore stale state.
                r.prepare_generation = r.prepare_generation + 1
                r.pending.prepare = nil
                r.state, r.reason = "preparing", "read-only readiness observation pending"
            end
            invoke(r, "status", r.status_generation)
        end
        return snapshot(r)
    end
    function api.capacity()
        return {profile=cap.name,revision=cap.revision,source_sha256=cap.source_sha256,
            scope="RETAINED_VALUE_CHARGES_NOT_RSS",requests_used=count,requests_limit=cap.sdk_history,
            retained_charge_bytes=retained_charge,retained_charge_limit_bytes=cap.sdk_retained_bytes,
            result_reservation_bytes=cap.sdk_result_bytes,request_charge_limit_bytes=cap.sdk_request_bytes,
            record_overhead_bytes=cap.sdk_record_overhead,eviction=false}
    end
    function api.preflight(new_requests)
        assert(type(new_requests)=="number" and new_requests>=0 and new_requests%1==0
            and new_requests<=cap.sdk_history,"bounded proposed request count required")
        local out=api.capacity()
        out.requested=new_requests
        out.worst_case_new_charge_bytes=new_requests*(cap.sdk_request_bytes+cap.sdk_result_bytes+cap.sdk_record_overhead)
        out.fits=count+new_requests<=cap.sdk_history and
            retained_charge+out.worst_case_new_charge_bytes<=cap.sdk_retained_bytes
        out.reserved=false -- Advisory snapshot; each actual prepare reserves again.
        return out
    end
    return api
end

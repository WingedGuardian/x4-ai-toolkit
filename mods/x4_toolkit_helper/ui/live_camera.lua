-- Bounded camera adapter. No registration, FFI declarations, pipe or automatic actions.
-- READ provenance (native X4 9.0 UI):
-- ego_detailmonitor/menu_map.lua:5497-5500 gates target view, then targets and sets it.
-- menu_map.lua:5544-5583 requires another object in the player's sector and checks
-- SetSofttarget's boolean result. :5358 restores cockpit with force=true.
-- menu_followcamera.lua:8,219,257 declares and reads GetExternalViewRefObject.
-- These callers establish argument shapes, NOT runtime qualification of this adapter.

return function(deps)
    assert(type(deps) == "table", "camera dependencies required")
    local native = deps.native
    assert(type(native) == "table", "native wrappers required")
    for _, name in ipairs({ "GetPlayerID", "GetPlayerControlledShipID",
        "GetContextByClass", "IsExternalViewDisabled",
        "IsPlayerCameraTargetViewPossible", "SetSofttarget",
        "SetPlayerCameraTargetView", "GetExternalViewRefObject",
        "SetPlayerCameraCockpitView" }) do
        assert(type(native[name]) == "function", "missing native wrapper: " .. name)
    end
    for _, name in ipairs({ "resolve_issued", "is_valid", "epoch", "now" }) do
        assert(type(deps[name]) == "function", "missing dependency: " .. name)
    end
    local active
    local api = {}
    local function result(status, reason, ticket, record)
        local attempts = {
            softtarget = record ~= nil and record.softtarget_attempted == true,
            target_view = record ~= nil and record.camera_attempted == true,
            cockpit = record ~= nil and record.cockpit_attempted == true,
        }
        return { status = status, reason = reason, ticket = ticket, attempts = attempts,
            action_attempted = attempts.softtarget or attempts.target_view or attempts.cockpit }
    end
    local function finite(v)
        return type(v) == "number" and v == v and v > -math.huge and v < math.huge
    end
    local function epoch()
        local v = deps.epoch()
        assert((type(v) == "string" and v ~= "") or finite(v), "invalid epoch")
        return v
    end
    local function now()
        local v = deps.now()
        assert(finite(v), "invalid monotonic time")
        return v
    end
    local function nonzero(id)
        return id ~= nil and id ~= false and id ~= 0 and id ~= ""
    end
    local function identity(record)
        assert(epoch() == record.epoch, "UI epoch changed")
        local id = deps.resolve_issued(record.token)
        assert(nonzero(id) and id == record.id, "issued identity changed or absent")
        assert(deps.is_valid(id) == true, "target invalid")
        return id
    end
    local function eligible(id)
        assert(native.IsExternalViewDisabled() == false, "external view unavailable")
        assert(native.IsPlayerCameraTargetViewPossible(id, true) == true, "target view unavailable")
        local player = native.GetPlayerID()
        assert(nonzero(player), "player unavailable")
        assert(id ~= native.GetPlayerControlledShipID(), "target is controlled ship")
        local sector = native.GetContextByClass(player, "sector", false)
        local targetsector = native.GetContextByClass(id, "sector", true)
        -- Stricter than native map's targetsector==0 fallback: never widen unknown scope.
        assert(nonzero(sector) and nonzero(targetsector) and sector == targetsector,
            "target outside known player sector")
    end
    local function fail(reason, uncertain)
        local record = active
        active = nil
        return result(uncertain and "UNCERTAIN" or "REFUSED", tostring(reason), nil, record)
    end

    function api.prepare(token)
        if active then return result("REFUSED", "camera operation already active") end
        if type(token) ~= "string" or token == "" then
            return result("REFUSED", "issued token required")
        end
        local ok, record = pcall(function()
            local e = epoch()
            local id = deps.resolve_issued(token)
            assert(nonzero(id) and deps.is_valid(id) == true, "target not issued or invalid")
            eligible(id)
            assert(epoch() == e, "UI epoch changed during preparation")
            return { token = token, id = id, epoch = e, prepared_at = now(), phase = "prepared" }
        end)
        if not ok then return fail(record) end
        record.ticket = {} -- opaque identity; all trusted state stays private
        active = record
        return result("PREPARED", "no writes performed", record.ticket)
    end

    function api.execute(ticket)
        local record = active
        if not record or ticket ~= record.ticket or record.phase ~= "prepared" then
            return result("REFUSED", "not a current unused preparation")
        end
        -- Consume before any native write; thrown/lost replies must not enable retries.
        record.phase = "consumed"
        local ok, stamp = pcall(function()
            local id = identity(record)
            eligible(id)
            local t = now()
            assert(t >= record.prepared_at and t - record.prepared_at <= 5,
                "preparation expired or clock reversed")
            assert(epoch() == record.epoch, "UI epoch changed before write")
            return t
        end)
        if not ok then return fail(stamp) end
        record.softtarget_attempted = true
        local targeted, success = pcall(native.SetSofttarget, record.id, "")
        if not targeted then return fail(success, true) end
        if success == false then return fail("native targeting refused") end
        if success ~= true then return fail("native targeting returned no boolean verdict", true) end
        -- Recheck after targeting in case that native call invalidated the world context.
        local checked, why = pcall(function() identity(record); eligible(record.id) end)
        if not checked then return fail(why, true) end
        record.camera_attempted = true
        local called, err = pcall(native.SetPlayerCameraTargetView, record.id, true)
        if not called then return fail(err, true) end
        record.executed_at = stamp
        record.last_at = stamp
        record.phase = "requested"
        return result("REQUESTED", "camera setter returned; completion unverified", ticket, record)
    end

    function api.observe(ticket)
        local record = active
        if not record or ticket ~= record.ticket or
            (record.phase ~= "requested" and record.phase ~= "verified") then
            return result("REFUSED", "no observable camera request")
        end
        local ok, observed = pcall(function()
            identity(record)
            local t = now()
            assert(t >= record.last_at, "clock reversed")
            record.last_at = t
            local target = native.GetExternalViewRefObject()
            assert(target ~= nil and type(target) == type(record.id), "external reference unavailable")
            if type(target) == "number" then
                assert(finite(target) and target >= 0 and target % 1 == 0, "external reference invalid")
            end
            return { time = t, target = target }
        end)
        if not ok then return fail(observed, true) end
        -- Zero elapsed time is not an independent later observation.
        if observed.time <= record.executed_at then
            return result("PENDING", "await a later update", ticket, record)
        end
        if record.phase == "requested" and observed.time - record.executed_at > 5 then
            active = nil
            return result("UNVERIFIED_TIMEOUT", "no timely independent target verification", nil, record)
        end
        if observed.target == record.id then
            record.phase = "verified"
            return result("TARGET_VERIFIED", "external reference equals issued target; not visual proof", ticket, record)
        end
        if record.phase == "verified" then
            active = nil
            return result("DIVERGED", "camera target changed; restoration ownership relinquished", nil, record)
        end
        return result("PENDING", "external reference does not yet match", ticket, record)
    end

    function api.restore_cockpit(ticket)
        local record = active
        if not record or ticket ~= record.ticket or record.phase ~= "verified" then
            return result("REFUSED", "no verified adapter-owned target to restore")
        end
        local ok, why = pcall(function()
            identity(record)
            eligible(record.id)
            assert(now() >= record.last_at, "clock reversed")
            assert(native.GetExternalViewRefObject() == record.id, "camera ownership changed")
        end)
        if not ok then return fail(why) end
        active = nil -- one attempt, including a setter exception
        record.cockpit_attempted = true
        local called, err = pcall(native.SetPlayerCameraCockpitView, true)
        if not called then return result("UNCERTAIN", tostring(err), nil, record) end
        return result("RESTORE_REQUESTED_UNVERIFIED", "native cockpit setter returned; no attested mode getter", nil, record)
    end

    return api
end

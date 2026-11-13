-- Read-only native build queue observation. No registration, transport, or writes.
-- Native contract/provenance and runtime limits: its live-transactions-qualification development record (in the toolkit's git repository, not in release downloads).
local M = {}
local MAX_PHASE = 32
local MAX_TEXT = 256

local function integer(v, max)
    return type(v) == "number" and v == v and v >= 0 and v <= max and v % 1 == 0
end

function M.new(deps)
    assert(type(deps) == "table", "dependencies required")
    local ffi, C = assert(deps.ffi), assert(deps.C)
    local resolve = assert(deps.resolve_issued_station)
    local clock = assert(deps.clock)
    assert(type(resolve) == "function" and type(clock) == "function", "dependency contract")
    -- Reuse the actual native names: independent anonymous substitutes can disagree
    -- with function declarations already installed by the game's UI.
    local ok = pcall(function()
        if not pcall(ffi.typeof, "UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
        if not pcall(ffi.typeof, "BuildTaskID") then ffi.cdef("typedef uint64_t BuildTaskID;") end
        if not pcall(ffi.typeof, "BuildTaskInfo") then
            ffi.cdef[[
                typedef struct {
                    BuildTaskID id;
                    UniverseID buildingcontainer;
                    UniverseID component;
                    const char* macro;
                    const char* factionid;
                    UniverseID buildercomponent;
                    int64_t price;
                    bool ismissingresources;
                    uint32_t queueposition;
                } BuildTaskInfo;
            ]]
        end
        -- Existing names can have been declared incorrectly by another UI module.
        -- Compare a private primitive-only layout, never pass its buffer to C.
        local expected = ffi.typeof([[struct {
            uint64_t id;
            uint64_t buildingcontainer;
            uint64_t component;
            const char* macro;
            const char* factionid;
            uint64_t buildercomponent;
            int64_t price;
            bool ismissingresources;
            uint32_t queueposition;
        }]])
        for _, alias in ipairs({"UniverseID", "BuildTaskID"}) do
            assert(ffi.sizeof(alias) == ffi.sizeof("uint64_t") and
                ffi.istype("uint64_t", ffi.new(alias)), "incompatible native ID alias")
        end
        assert(ffi.sizeof("BuildTaskInfo") == ffi.sizeof(expected) and
            ffi.alignof("BuildTaskInfo") == ffi.alignof(expected), "incompatible task layout")
        for _, field in ipairs({"id", "buildingcontainer", "component", "macro",
            "factionid", "buildercomponent", "price", "ismissingresources", "queueposition"}) do
            local offset, bitpos = ffi.offsetof("BuildTaskInfo", field)
            assert(offset == ffi.offsetof(expected, field) and bitpos == nil,
                "incompatible task field offset")
        end
        -- A single zeroed probe is safe only after size/alignment have matched.
        -- Field reads plus an unsigned-boundary roundtrip distinguish signed or
        -- floating 32-bit substitutes that have identical size and offsets.
        local probe = ffi.new("BuildTaskInfo")
        for _, field in ipairs({"id", "buildingcontainer", "component", "buildercomponent"}) do
            assert(ffi.istype("uint64_t", probe[field]), "incompatible task ID field")
        end
        assert(ffi.istype("int64_t", probe.price), "incompatible task price field")
        assert(ffi.istype("const char *", probe.macro) and
            ffi.istype("const char *", probe.factionid), "incompatible task text field")
        assert(type(probe.ismissingresources) == "boolean", "incompatible task flag")
        probe.queueposition = 4294967295
        assert(type(probe.queueposition) == "number" and probe.queueposition == 4294967295,
            "incompatible task queue field")
        ffi.cdef[[
            uint32_t GetNumBuildTasks(UniverseID containerid, UniverseID buildmoduleid,
                                     bool isinprogress, bool includeupgrade);
            uint32_t GetBuildTasks(BuildTaskInfo* result, uint32_t resultlen,
                                  UniverseID containerid, UniverseID buildmoduleid,
                                  bool isinprogress, bool includeupgrade);
        ]]
    end)

    local function id(v, zero)
        -- Never round a native uint64 through a Lua double.
        if type(v) ~= "cdata" or not ffi.istype("uint64_t", v) then
            error("invalid uint64 field")
        end
        local s = tostring(v)
        if not s:match("^%d+ULL$") or (not zero and s == "0ULL") then
            error("invalid uint64 identity")
        end
        return s
    end
    local function money(v)
        if type(v) ~= "cdata" or not ffi.istype("int64_t", v) then error("invalid price") end
        local s = tostring(v)
        if not s:match("^%-?%d+LL$") then error("invalid price representation") end
        return s
    end
    local function text(v)
        if v == nil or v == ffi.NULL then error("missing text") end
        -- Scan only a bounded prefix; do not call unbounded ffi.string(pointer).
        local chars = {}
        for i = 0, MAX_TEXT do
            local n = tonumber(v[i])
            if n == 0 then
                local s = table.concat(chars)
                if s == "" or s == "?" or s:find("[%c]") then error("unknown text") end
                return s
            end
            if i == MAX_TEXT then error("text limit") end
            chars[#chars + 1] = string.char(n < 0 and n + 256 or n)
        end
    end
    local function now()
        local v = clock()
        if type(v) ~= "number" or v ~= v or v < 0 or v == math.huge then error("invalid clock") end
        return v
    end

    local function capture(token)
        local result = {
            version = 1, status = "refused", atomic = false,
            scope = "one issued station; active and waiting; upgrades included",
            limit_per_phase = MAX_PHASE, rows = {}, phases = {}, issues = {},
            enumeration_complete = false,
        }
        local function issue(code, phase, index)
            result.issues[#result.issues + 1] = {code = code, phase = phase, index = index}
        end
        if not ok then issue("native_declaration_unavailable"); return result end
        local resolved, station = pcall(resolve, token)
        if not resolved or station == nil then issue("unissued_station"); return result end
        local identified, stationtext = pcall(id, station, false)
        if not identified then issue("invalid_station_identity"); return result end
        result.station = stationtext
        local started, start = pcall(now)
        if not started then issue("clock_unavailable"); return result end
        result.start = start
        result.status = "partial"
        local seen = {}
        for _, phase in ipairs({{name = "active", active = true}, {name = "waiting", active = false}}) do
            local record = {phase = phase.name, includeupgrade = true, read = "unknown"}
            result.phases[#result.phases + 1] = record
            local succeeded = pcall(function()
                local count = C.GetNumBuildTasks(station, 0, phase.active, true)
                if not integer(count, 4294967295) then error("invalid_count") end
                record.before = count
                if count > MAX_PHASE then record.read = "overflow"; issue("phase_overflow", phase.name); return end
                -- A zero remains observable only after the second native count agrees.
                local returned = 0
                if count > 0 then
                    local buffer = ffi.new("BuildTaskInfo[?]", count)
                    returned = C.GetBuildTasks(buffer, count, station, 0, phase.active, true)
                    if not integer(returned, count) then
                        record.read = "invalid"; issue("returned_out_of_bounds", phase.name); return
                    end
                    record.returned = returned
                    for i = 0, returned - 1 do
                        local valid, row = pcall(function()
                            local native = buffer[i]
                            local r = {
                                phase = phase.name, id = id(native.id, false),
                                buildingcontainer = id(native.buildingcontainer, false),
                                component = id(native.component, true),
                                macro = text(native.macro), faction = text(native.factionid),
                                buildercomponent = id(native.buildercomponent, true),
                                price = money(native.price),
                                missingresources = native.ismissingresources,
                                queueposition = tonumber(native.queueposition),
                            }
                            if type(r.missingresources) ~= "boolean" or not integer(r.queueposition, 4294967295) then
                                error("invalid fields")
                            end
                            return r
                        end)
                        if not valid then
                            issue("invalid_row", phase.name, i)
                        else
                            if seen[row.id] then issue("duplicate_or_phase_overlap", phase.name, i) end
                            seen[row.id] = true
                            result.rows[#result.rows + 1] = row
                        end
                    end
                else
                    record.returned = 0
                end
                local after = C.GetNumBuildTasks(station, 0, phase.active, true)
                if not integer(after, 4294967295) then error("invalid_count") end
                record.after = after
                if count ~= returned or count ~= after then
                    record.read = "changed"; issue("phase_count_changed", phase.name)
                else
                    record.read = "observed"
                end
            end)
            if not succeeded then issue("native_read_failed", phase.name) end
        end
        local ended, finish = pcall(now)
        if not ended or finish < start then
            issue("invalid_end_clock")
        else
            result.finish = finish
        end
        -- Check the resolver again so a revoked ID/UI-generation change cannot pass.
        local stillresolved, current = pcall(resolve, token)
        local same, currenttext = pcall(id, current, false)
        if not stillresolved or not same or currenttext ~= stationtext then issue("station_binding_changed") end
        if #result.issues == 0 then
            result.status = "complete"
            result.enumeration_complete = true
        end
        return result
    end
    return {capture = capture}
end
return M

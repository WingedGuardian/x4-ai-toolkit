-- X4 Toolkit live query channel: legacy reads/pause plus bound automation transport.
-- Automation is a separate structured route with session identity, immutable
-- preparation and durable host dispatch; it never uses the legacy pause wrapper.
--
-- THE LEGACY WRITES: `pause` and `unpause`, held in WRITE_VERBS. Neither takes an
-- argument or touches a savegame. Structured automation routes are separate: their
-- fixed registered actions require bound session/preparation and host journaling.
-- No arbitrary script evaluation is exposed by either route.
--
-- Answers a fixed vocabulary of questions from an external Python server over a
-- named pipe, so the toolkit can ask the RUNNING engine something without a
-- quit-and-relaunch. Complement to the uidata probe: that one is an offline bulk
-- oracle (uidata.xml is 61 bytes while the game runs), this one is live spot checks.
--
-- SHAPE COPIED, NOT COMPOSED. Modelled line-for-line on
-- sn_mod_support_apis/ui/time/pipe_time.lua, which is a working 90-line example of
-- this exact round trip. A schema tells you what is well-formed; it cannot tell you
-- what is wired up, and composing from one cost three play sessions on 2026-08-27.
--
-- WE ARE THE CLIENT. The external Python creates the pipe; pipes.lua opens a client
-- handle to it and does not care who served it. That is why this mod needs no MD, no
-- Pipe_Server_Host registration and no permissions.json grant.
--
-- CONTRACTS THIS FILE KEEPS (each because something got it wrong before):
--  * A LOAD MARKER is the first thing emitted, before any pipe logic. Absent marker
--    means the mod did not load, and nothing downstream is worth debugging.
--    "PROVE IT RAN BEFORE DEBUGGING WHAT IT DID."
--  * Every reply declares its own BYTE length and checksum.
--    CORRECTED 2026-08-29 after reading the PACKED pipes.lua out of its .cat: the
--    unhandled ERROR_MORE_DATA at :698 is on the READ path (the game reading OUR
--    COMMAND), and it does NOT truncate. winpipe exposes only ERROR_IO_PENDING and
--    ERROR_NO_DATA, so 234 falls through to :720 error(), is caught by Read_Pipe's
--    pcall, reaches Poll_For_Reads:560 -> Close_Pipe, and DESTROYS THE PIPE. Replies
--    fail the same way by the api's own docs. So an over-long message is a loud
--    total teardown in either direction, never a quiet short answer -- which means
--    SIZE MUST BE BOUNDED BEFORE SENDING. The length and checksum catch a short
--    read; they cannot prevent a teardown. Any unbounded verb caps itself.
--  * Three states, never a bare zero: OK / ABSENT / ERR. "the engine says no such
--    macro" and "we could not ask" must never share a representation.
--  * string.format("%.0f", n) for the checksum, NEVER tostring(n). Lua renders a
--    number above ~1e14 in exponent form, so tostring(3000000000) is "3e+09" and the
--    Python side's int() would raise on a perfectly correct checksum.

local PIPE  = "x4live"
local PROTO = 1

--: BUILD FINGERPRINT -- the first 8 hex of a sha256 over this file with this very
--: line masked out. Reported by `probe`, so the caller can prove the GAME is running
--: the file on DISK rather than assuming it.
--:
--: ⚠ THIS EXISTS BECAUSE "A DEPLOYED FILE IS NOT A LOADED FILE" COST TWO CYCLES IN
--: ONE SESSION. The first cost was believing a reload had happened; the second was a
--: staleness check that fingerprinted on ONE new field (`our_ffi=`). That field was
--: added in the PREVIOUS build, so it proved "not the build before last" and said
--: nothing about the current one -- a check whose pass branch did not mean what it
--: claimed. A content hash cannot have that failure: it changes whenever the file
--: changes, including changes nobody thought to enumerate.
--:
--: Kept honest by `test_the_BUILD_constant_matches_the_file`, so editing the lua and
--: forgetting to re-stamp this fails the suite rather than silently lying in game.
local BUILD = "3dbdc1ef"
local TAG_CMD, TAG_REPLY = "MQ", "MR"

-- Cap on echo, the ramp instrument. Generous: the point of the ramp is to FIND the
-- real ceiling, so this must sit well above any plausible one and only exists to
-- stop a typo allocating gigabytes.
local ECHO_MAX = 1024 * 1024

local armed = false

--: The F124 pipe unref guard for THIS helper's own pipes (built near the end of the
--: file, reported by `probe`). nil until the named pipes api has been required.
local pipe_guard = nil

--: RELOAD BOOKKEEPING -- how a caller tells that the game RE-EXECUTED this chunk.
--:
--: ⚠ MEASURED IN GAME 2026-08-29, with controls in both directions. The engine
--: re-executes every registered UI lua file on a full UI reload, and TWO ordinary
--: actions cause one:
--:   * pressing ALT-ENTER (a graphics MODE change) -- 2 presses, 2 reloads;
--:   * LOADING A SAVE -- new chunk, and DEBUG.TXT's timestamp clock resets with it
--:     (1289.31 -> 143.77). NB that is debug.txt's clock, not getElapsedTime(); the two
--:     are different, which is a distinction this file got wrong once already (below).
--: Alt-TAB does NOT: 75.7 s of foreground time here and 1190 s in an earlier session,
--: zero reloads across both. The variable is the mode change, not the focus change.
--:
--: A reload builds a FRESH chunk, so `issued_ids` starts empty and every id the caller
--: holds stops resolving. For a save load that is REQUIRED (see Init). For an
--: alt-enter it is merely surprising -- and until `component` learned to say so, the
--: caller could not tell a reload from a fabricated id: both produced one refusal with
--: identical wording, and the wording blamed the caller.
--:
--: ★★ NO LUA STATE SURVIVES A UI RELOAD. MEASURED IN GAME 2026-08-29/30 (the session
--: crossed midnight), and it is the single fact that explains every other observation
--: of that session.
--:
--: This block briefly carried a second field, `incarnation`: a counter kept on a
--: SHARED GLOBAL (`_G`) and incremented once per chunk execution. Across two real
--: reloads it read 1, then 1. The counter was not wrong -- `_G` ITSELF DOES NOT
--: PERSIST. The engine rebuilds the whole lua environment, not merely our chunk.
--:
--: Three things follow, and the second is why the field was worth building anyway:
--:  * A count of reloads CANNOT be kept game-side. Any such detection has to live in
--:    the client, or in a value that differs per chunk by construction.
--:  * The design we nearly shipped -- persist `issued_ids` across reloads via a global
--:    so alt-enter stops invalidating ids -- was not merely UNSAFE, it was IMPOSSIBLE.
--:    No global could have carried the allowlist either. We rejected it for the right
--:    reason (after a save load a REUSED handle returns another object's data under
--:    the id you asked about) and got a second, stronger one for free.
--:  * It unifies the rest: no stale closures accumulate, no duplicate RegisterEvent
--:    listeners build up, and the allowlist empties -- ONE cause, not three.
--:
--: ⚠ The counter was also a textbook instrument-that-cannot-move: only its "no reload
--: happened" branch was ever reachable. It was caught ONLY because the prediction was
--: written down before the second reload, so the result was able to contradict it.
--: Recorded here rather than quietly deleted.
--:
--:   loaded_at   getElapsedTime() at chunk load. ⚠ NOT session time, and NOT per-save.
--:               MEASURED 0.89 then 0.82 across two reloads that debug.txt timestamped
--:               254.56 and 425.42 -- a DIFFERENT CLOCK, which resets with the UI. So
--:               this is usable ONLY as a change detector: a fresh chunk almost always
--:               reports a different value. Treat a difference as strong evidence of a
--:               reload and a match as weak, since two chunks can collide at 2 decimal
--:               places. An earlier version of this comment said it "goes BACKWARDS on
--:               a save load, because the clock is per-save" -- that was reasoned from
--:               debug.txt timestamps rather than from this function, and was wrong.
local LOADED_AT = (type(getElapsedTime) == "function") and getElapsedTime() or -1

--: Set true by the save-load hook in Init if it EVER runs. MEASURED 2026-08-29 across
--: a game start AND a save load: it never has -- 0 occurrences of its log line, while
--: `RegisterEvent absent` and `could not hook game load` are also 0, so it registers
--: successfully and simply never fires. Reported by `probe` so its deadness is VISIBLE
--: rather than assumed. See Init for why it cannot fire and what actually protects us.
local load_hook_fired = false

-- Forward-declared as LOCALS. The menus lua environment is SHARED between every
-- mod that loads through ui.xml, so a global named `pipes` or `Init` is a collision
-- waiting to happen -- pipe_time.lua gets away with it only because it is first.
--: X4 runs LuaJIT (5.1), where this is the global `unpack`; the offline lupa harness
--: may be 5.2+, where it moved to `table.unpack`. Bound once so the dispatch does not
--: have to care, and so a missing one fails HERE rather than inside a verb.
local table_unpack = table.unpack or unpack

local pipes = nil
local Init
local on_message          -- forward-declared: arm_read() references it before its definition
local schedule_rearm      -- forward-declared: arm_read() re-arms through it on failure

--: Set by reply(). The dispatch error path consults it so a verb that replied and
--: THEN raised cannot emit a SECOND frame for one seq. Two frames for one question
--: desync the FIFO, and the NEXT query reads the stale one and fails clause 5 --
--: surfacing the failure one question AFTER its cause, the worst possible place.
local last_replied_seq = nil

--: Set by the dispatcher while a WRITE verb runs, cleared after. reply() consults it so
--: that every write frame leads with its advisory row -- see WRITE_VERBS.
local writing_verb = nil
local automation_capture = nil

--: A READ-BACK value renders true / false / unknown; a flag the VERB chose renders
--: yes / no / unknown. Two vocabularies on purpose, so a mis-wired field reads as wrong
--: rather than as plausible.
local function tri(v)
    if v == true then return "true" elseif v == false then return "false" end
    return "unknown"
end
local function yn(v)
    if v == true then return "yes" elseif v == false then return "no" end
    return "unknown"
end

--: At most n bytes of s, never ending inside a UTF-8 sequence. The host decodes a frame
--: strictly, so a split character would turn the whole reply into a non-answer.
local function utf8_prefix(s, n)
    if #s <= n then return s end
    local cut = n
    while cut > 0 do
        local b = s:byte(cut + 1)
        if b == nil or b < 0x80 or b >= 0xC0 then break end
        cut = cut - 1
    end
    return s:sub(1, cut)
end

--: The advisory row every write reply leads with. `write=yes` is FIRST and is the
--: structural marker: a caller tests the first token of the first field, never a
--: substring. `reason` names which guard decided the outcome, so two refusals that
--: share a status can still be told apart.
local function write_advisory(t)
    return table.concat({
        "write=yes",
        "verb=" .. tostring(t.verb),
        "reason=" .. tostring(t.reason),
        "acted=" .. yn(t.acted),
        "before=" .. tri(t.before),
        "after=" .. tri(t.after),
        "want=" .. tri(t.want),
        "agree=" .. yn(t.agree),
        "owner=" .. tostring(t.owner or "unknown"),
        "ownerreset=" .. yn(t.ownerreset),
        "build=" .. BUILD,
    }, " ")
end

-- Re-arm state. See schedule_rearm() for why the delay is not optional.
local REARM_DELAY   = 2.0   -- seconds: the FAST delay, after a client or a fault
--: IDLE BACK-OFF (2026-11-14). With no x4live process serving the pipe, every arm meets
--: a missing pipe, Close_Pipe and our sentinel -- forever, every REARM_DELAY. That churn
--: is expected, but it is also the only helper activity while nobody is using the
--: channel, so it is cut: each consecutive IDLE teardown doubles the delay up to
--: REARM_IDLE_MAX, and the first request on an arm (or any non-idle teardown) resets it.
--: The cost is connection latency for the NEXT client, up to REARM_IDLE_MAX; the Python
--: side waits at least `_livepipe.CONNECT_WINDOW_MIN_S` for that reason, and the two are
--: pinned together by tests/test_livepipe.py.
local REARM_IDLE_MAX = 10.0
local rearm_delay   = REARM_DELAY -- the delay the NEXT idle re-arm will use
local requests_on_arm = 0   -- MQ frames handled since the current arm began
local in_request    = false -- true while a verb runs; a teardown then is a FAULT
local rearm_logs    = 0     -- how many IDLE lines we have written
local last_rearm_log = -1e9 -- sentinel_clock() of the last one
--: CLOSED lines have their OWN budget, so each x4live call's end stays visible after the
--: idle lines have spent theirs: at most one per DISCONNECT_LOG_GAP real seconds. A call
--: cannot end faster than one fast re-arm (2 s), so this suppresses only a burst.
local DISCONNECT_LOG_GAP = 1.0
local last_disconnect_log = -1e9

--: The sentinel's own clock: the throttles and the `t=` field. GetCurRealTime, not
--: getElapsedTime. READ by static disassembly of X4.exe (2026-11-14, BLIND-SPOTS F124):
--: getElapsedTime's C body fetches the UI host from registry slot 1 and calls through it
--: with no NULL check -- the 11-14 crash site -- while GetCurRealTime reads one
--: process-global double under a lock and touches no host. It is the clock vanilla menus
--: and Mod Support APIs' frame detector use in this same `menus` environment. It keeps
--: advancing while the game is PAUSED (INFERRED: the frame detector skips any frame whose
--: GetCurRealTime is unchanged, and a paused game was MEASURED 2026-09-13 to keep
--: answering on the pipe that detector polls). Being process-global, it is NOT expected
--: to reset on a save load or UI reload (UNMEASURED); the throttle STATE does reset then,
--: because the chunk is rebuilt, so each load gets three fresh idle lines.
--: Falls back to getElapsedTime only if GetCurRealTime is absent; 0 if neither answers.
local function read_clock(f)
    if type(f) ~= "function" then return nil end
    local ok, t = pcall(f)
    if ok and type(t) == "number" then return t end
    return nil
end
local function sentinel_clock()
    return read_clock(GetCurRealTime) or read_clock(getElapsedTime) or 0
end

-- --------------------------------------------------------------------------- --
-- framing
-- --------------------------------------------------------------------------- --

-- djb2 over BYTES. Must agree exactly with _livepipe.checksum on the Python side.
-- #s and string.byte are both byte operations in Lua, which is why the Python side
-- encodes to UTF-8 first rather than iterating codepoints.
local function checksum(s)
    local h = 5381
    for i = 1, #s do
        h = (h * 33 + s:byte(i)) % 4294967296
    end
    return h
end

local function split_tabs(s)
    -- Preserves EMPTY fields, which string.gmatch("[^\t]+") silently collapses --
    -- and a collapsed empty argument shifts every later one by a position.
    local out, pos = {}, 1
    while true do
        local i = string.find(s, "\t", pos, true)
        if not i then
            out[#out + 1] = string.sub(s, pos)
            return out
        end
        out[#out + 1] = string.sub(s, pos, i - 1)
        pos = i + 1
    end
end

-- MOVED ABOVE reply(): a Lua `local` is only in scope AFTER its
-- declaration, so these sat 190 lines BELOW the function that enforces
-- them and resolved to nil globals there. The lupa suite caught it at
-- once -- the mod stopped replying to a plain ping, because comparing a
-- length against nil raises.
--: Bound REPLIES before sending. An over-long message does not truncate, it tears
--: the pipe down (see the header), so an unbounded enumeration is a connection
--: killer rather than a short answer. 32000 is well under the 524,288 bytes proven to
--: round-trip in game, which leaves room for the frame header and any single
--: oversized row.
local MAX_PAYLOAD = 32000

--: Reserved out of MAX_PAYLOAD for the header line, which is prepended AFTER the row
--: loop and so cannot be measured during it.
--:
--: ⚠ MEASURED IN GAME 2026-08-29: without this, `ships argon` capped its rows at
--: exactly 32000 and then added a 143-byte header, shipping 32143 bytes against a
--: "32000-byte" budget. Harmless in itself -- the proven ceiling is 524,288 -- but the
--: number did not mean what it said, and a budget you cannot trust is not a budget.
--:
--: ★ The test was complicit: it asserted `<= 32000 + 200`. That 200-byte slack was
--: added for comfort and is exactly what let a 143-byte overrun through. A tolerance
--: nobody derived is a place for defects to live; the assertion is now exact.
local HEADER_RESERVE = 512

--: Per-VALUE budget for `recon --contents`. Deliberately far below ROW_BUDGET: one
--: fat table must not be able to crowd out the other 100-odd probe results, and the
--: point of the mode is to see the SHAPE of a payload, not to exfiltrate all of it.
--: A truncated cell says `+N-more` rather than ending silently.
local CONTENTS_BUDGET = 400
local ROW_BUDGET = MAX_PAYLOAD - HEADER_RESERVE

--: THE ONE RENDERER for a table's CONTENTS, shared by `recon --contents` (one level, two
--: with `--deep`) and `macro --contents` (two levels). Every property is load-bearing:
--:  * DEPTH-BOUNDED, so there is no recursion to GUARD: a cyclic engine table cannot hang
--:    us, because below `depth` a table renders as `<table>` -- an honest "there is more
--:    here", never a silent omission.
--:  * BOUNDED per value by CONTENTS_BUDGET, and a cut says `+N-more`: an over-long reply
--:    TEARS THE PIPE DOWN (F74) rather than truncating.
--:  * SANITISED per KEY and per STRING LEAF, never over the assembled cell. A tab or
--:    newline would split the frame's fields and a comma would forge a sibling inside
--:    `{...}`; scrubbing the assembled cell instead would also erase the commas of a
--:    nested table's own rendering.
--:  * SORTED keys, so two runs are diffable; pairs() order is not guaranteed stable.
--: At depth 1 the output is byte-identical to recon's previous one-level renderer --
--: `recon-20260830-211605.tsv` was recorded with it.
--:
--: TWO PASSES, so one fat nested table cannot push its small siblings out. MEASURED in
--: review 2026-09-14: rendered in key order with a full budget each, `{weapons={aaa=<60
--: strings>, zzz=1}}` came back `weapons={} +2-more` -- the inner table filled its own budget
--: and then fitted nowhere. Scalar cells are laid out first; the nested tables then SHARE
--: what is left, each announcing its own cut; the output keeps sorted key order.
--: Trade-off, accepted (review round 2): when the scalar cells alone use the budget, a small
--: nested table shows as `<table>` where one pass would have rendered it -- less, never wrong.
--: A string is cut with `utf8_prefix`: a 40-byte cut through a multibyte character made the
--: reply invalid UTF-8, and the host decodes strictly (same review).

--: Taken out of a nested cell's share for the child's own braces and "+N-more" marker,
--: which are appended outside the budget the child is given.
local CONTENTS_NEST_RESERVE = 16

local function render_table(v, depth, budget)
    budget = budget or CONTENTS_BUDGET
    local ks = {}
    for k in pairs(v) do ks[#ks + 1] = k end
    table.sort(ks, function(a, b) return tostring(a) < tostring(b) end)
    local function keyof(k) return (tostring(k):gsub("[|\t\n\r,]", " ")) end
    local cells, nested, fixed = {}, {}, 0
    for i, k in ipairs(ks) do
        local val, vt = v[k], type(v[k])
        if vt == "table" and depth > 1 then
            nested[#nested + 1] = i
        else
            local r
            if vt == "number" or vt == "boolean" then r = tostring(val)
            elseif vt == "string" then
                r = (((#val > 40) and (utf8_prefix(val, 40) .. "..") or val):gsub("[|\t\n\r,]", " "))
            else r = "<" .. vt .. ">" end
            cells[i] = keyof(k) .. "=" .. r
            fixed = fixed + #cells[i] + 1
        end
    end
    if #nested > 0 then
        local share = math.floor((budget - fixed) / #nested)
        for _, i in ipairs(nested) do
            local prefix = keyof(ks[i]) .. "="
            local room = share - #prefix - 1 - CONTENTS_NEST_RESERVE
            cells[i] = prefix .. ((room > 2) and render_table(v[ks[i]], depth - 1, room) or "<table>")
        end
    end
    local parts, used, shown = {}, 0, 0
    for i = 1, #ks do
        local cell = cells[i]
        if used + #cell + 1 > budget then break end
        parts[#parts + 1] = cell
        used = used + #cell + 1
        shown = shown + 1
    end
    return "{" .. table.concat(parts, ",") .. "}"
           .. ((shown < #ks) and (" +" .. (#ks - shown) .. "-more") or "")
end


--: An absolute bound on ENUMERATION, distinct from the bound on the REPLY.
--: The reply cap has always been enforced; the WORK never was. MEASURED offline with
--: the repo's own lupa harness at N=5000: the reply capped to 489 rows and the code
--: still made 40,492 engine calls -- and pipes.lua drains the FIFO inside a single
--: Time.Register_NewFrame_Callback, so that is one frame. Set far above any real
--: query (argon alone is ~2,000 objects galaxy-wide) so it never fires in normal use;
--: when it does fire it SAYS SO in the header rather than quietly returning less.
local MAX_ENUMERATE = 20000

local function reply(seq, status, payload, unbounded)
    payload = payload or ""
    if automation_capture then
        assert(writing_verb==nil and seq==automation_capture.seq,"invalid bound read reply")
        automation_capture.count=automation_capture.count+1
        assert(automation_capture.count==1,"bound read emitted multiple replies")
        automation_capture.status,automation_capture.payload=status,payload
        return
    end
    -- THE WRITE INVARIANT, enforced here once for the same reason as the size bound
    -- below: a write reply that does not lead with its advisory row is WRAPPED in one
    -- and turned into an ERR marked `reason=unwrapped acted=unknown`. A write verb that
    -- forgets its header, or raises before producing one, therefore still cannot ship
    -- a frame a caller would read as a verified result.
    if writing_verb ~= nil and payload:sub(1, 10) ~= "write=yes " then
        status = "ERR"
        payload = write_advisory({ verb = writing_verb, reason = "unwrapped" }) .. "\t"
                  .. "this WRITE verb's reply did not lead with its advisory row, so it "
                  .. "was wrapped here and marked untrusted. The pause state was NOT "
                  .. "verified -- check it with `x4live pausestate`. Original reply: "
                  .. (#payload > 2000
                      and (utf8_prefix(payload, 2000) .. " [+"
                           .. (#payload - #utf8_prefix(payload, 2000)) .. " bytes cut]")
                      or payload)
    end
    -- THE choke point. Every verb goes through here, so the bound is enforced once
    -- rather than remembered in each of them -- which is how verbs.ext and verbs.macro
    -- came to send 40,025 and 40,017 bytes against a 32,000-byte cap while the header
    -- of this file said "any unbounded verb caps itself".
    --
    -- An over-long frame is not a truncation, it is a total teardown of the pipe in
    -- both directions. So it must never be SENT: it becomes an ERR naming the size,
    -- which is a diagnosis the caller can act on.
    -- `unbounded` is opt-in, per call, and exactly one verb passes it: `echo`, whose
    -- entire purpose is to be larger than we think the transport allows so that
    -- `x4live ramp` can find the ENGINE's ceiling. Capping it made the ramp report OUR
    -- cap as the game's -- MEASURED: last_ok fell from 524288 to 16384 and the tool
    -- printed "the ceiling lies in (16384, 32768]" and exited 0. `_BUF`'s comment
    -- records the same failure happening once before at (60000, 65536].
    --
    -- A per-call flag rather than a size exemption: every other verb keeps the choke
    -- point, and a verb added later gets it without anyone remembering to ask.
    if (not unbounded) and #payload > MAX_PAYLOAD then
        status = "ERR"
        payload = "reply too large: " .. #payload .. " bytes exceeds MAX_PAYLOAD "
                  .. MAX_PAYLOAD .. " -- this verb produced an uncapped result set"
        -- A WRITE reply keeps its advisory even here: the cap must not strip what the
        -- wrap above guarantees.
        if writing_verb ~= nil then
            payload = write_advisory({ verb = writing_verb, reason = "too-large" })
                      .. "\t" .. payload
        end
    end
    -- seq is echoed as the RAW STRING it arrived as. Round-tripping it through
    -- tonumber/tostring risks the same exponent rendering as the checksum.
    local frame = table.concat({
        TAG_REPLY,
        tostring(PROTO),
        seq,
        status,
        string.format("%d", #payload),
        string.format("%.0f", checksum(payload)),
        payload,
    }, "\t")
    pipes.Schedule_Write(PIPE, nil, frame)
    last_replied_seq = seq
end

-- --------------------------------------------------------------------------- --
-- verbs -- a fixed dispatcher, not a console. MD has no eval and we ship no lua one.
-- TWO TABLES: `verbs` only reads, and WRITE_VERBS holds the entire write vocabulary --
-- `pause` and `unpause`, both argument-free. "Is this a write?" is a table lookup.
-- --------------------------------------------------------------------------- --

local function sorted_keys(t)
    local ks = {}
    for k in pairs(t) do ks[#ks + 1] = tostring(k) end
    table.sort(ks)
    return ks
end

-- --------------------------------------------------------------------------- --
-- FFI -- for the symbols the engine does NOT expose as bare globals
-- --------------------------------------------------------------------------- --
--
-- MEASURED IN GAME 2026-08-29 by the `probe` verb, which is exactly why it shipped
-- first. The split is not guesswork and it is not uniform:
--
--   bare globals : GetComponentData, IsValidComponent, ConvertStringTo64Bit,
--                  ConvertIDTo64Bit, GetMacroData, GetLibraryEntry
--   ffi only     : GetPlayerOccupiedShipID, GetPlayerID, GetComponentClass,
--                  the four faction-enumeration calls, GetObjectPositionInSector
--
-- It matches vanilla's own usage exactly: every bare-available symbol is one vanilla
-- calls bare, every nil one is a symbol vanilla declares in ffi.cdef and calls as
-- `C.*`. Had these verbs been written against bare globals as first planned, all
-- seven would have been nil and the whole slice would have failed in game.
--
-- REDEFINITION IS SAFE HERE and that is measured, not assumed: vanilla declares
-- `GetPlayerOccupiedShipID` identically in 10 files, `typedef uint64_t UniverseID`
-- in 27, and `UIPosRot` in 8 -- all sharing ONE FFI state. Our file loads after all
-- of them, so this block is a redefinition every time.
--
-- ⚠ SIGNATURES ARE COPIED CHARACTER-FOR-CHARACTER FROM VANILLA, never composed.
-- A wrong signature here is not a lua error, it is a memory-layout error.
--   helper.lua:154-161  UIPosRot        helper.lua:366  GetObjectPositionInSector
--   helper.lua:371,373  GetPlayerID, GetPlayerOccupiedShipID
--   menu_docked.lua:105 GetPlayerObjectID
--   menu_map.lua:587    GetComponentClass
--   menu_playerinfo.lua:185-186, 205-206  the faction enumeration pairs
--   menu_encyclopedia.lua:101  GetFactionRepresentative (the only vanilla cdef; menu_diplomacy.lua:1657 calls it)
local ffi, C = nil, nil
local ffi_ok, cdef_ok = false, false
do
    local ok, mod = pcall(require, "ffi")
    if ok and type(mod) == "table" then
        ffi, ffi_ok = mod, true
        -- pcall'd: if a future patch makes redefinition fatal, the ffi verbs degrade
        -- to a clean ERR and every bare-global verb keeps working.
        local cok, cerr = pcall(ffi.cdef, [[
            typedef uint64_t UniverseID;
            typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;
            UniverseID GetPlayerID(void);
            UniverseID GetPlayerObjectID(void);
            UniverseID GetPlayerOccupiedShipID(void);
            UniverseID GetFactionRepresentative(const char* factionid);
            const char* GetComponentClass(UniverseID componentid);
            uint32_t GetNumAllFactionShips(const char* factionid);
            uint32_t GetAllFactionShips(UniverseID* result, uint32_t resultlen, const char* factionid);
            uint32_t GetNumAllFactionStations(const char* factionid);
            uint32_t GetAllFactionStations(UniverseID* result, uint32_t resultlen, const char* factionid);
            UIPosRot GetObjectPositionInSector(UniverseID objectid);
            /* The faction list, for a future cross-faction `objects <sector>`. Copied
               VERBATIM from vanilla (customgame.lua:279 and :224, same pair again in
               menu_mapeditor.lua:70) rather than composed from the shape of the others:
               a signature guessed from a pattern is a crash, not a compile error.
               Declared now only so `probe` can report whether they are EXPORTED --
               indexing ffi.C for an undeclared symbol raises rather than returning nil,
               so an undeclared symbol cannot be probed at all. */
            uint32_t GetNumAllFactions(bool includehidden);
            uint32_t GetAllFactions(const char** result, uint32_t resultlen, bool includehidden);
        ]])
        cdef_ok = cok and true or false
        if cok then C = ffi.C
        else DebugError("X4TOOLKIT_LIVE ffi.cdef failed: " .. tostring(cerr)) end
    end
end

--: The ONE declaration the pause verbs need, guarded SEPARATELY from the block above.
--: Folded into it, a failure here would take every ffi READ verb down with the pause
--: verbs; alone, it costs only the verbs that cannot verify themselves without it.
--: Copied verbatim from vanilla, which declares it identically in three files
--: (helptext.lua:9, monitors.lua:96, crosshair handling.lua:124), so a redeclaration
--: is the attested case rather than a guess.
local pause_cdef_ok = false
if ffi_ok then
    local pok, perr = pcall(ffi.cdef, [[ bool IsGamePaused(void); ]])
    pause_cdef_ok = pok and true or false
    if not pok then
        DebugError("X4TOOLKIT_LIVE ffi.cdef IsGamePaused failed: " .. tostring(perr)
                   .. " -- pause, unpause and pausestate will refuse; the read verbs "
                   .. "are unaffected")
    end
end

-- --------------------------------------------------------------------------- --
-- the SESSION ID ALLOWLIST
-- --------------------------------------------------------------------------- --
--
-- ⚠ MEASURED over vanilla: of 888 `GetComponentData` call sites, ZERO pass an id
-- that did not come out of the engine in the same code path. There is therefore no
-- evidence in EITHER direction about what a fabricated or stale 64-bit id does, and
-- no defensive idiom to copy. An id typed at a CLI is precisely that unknown case.
--
-- So this channel inspects only ids it HANDED OUT itself. `IsValidComponent` is
-- still checked on top, because vanilla's own guards say handles go stale between
-- frames -- but the allowlist is what keeps a fabricated integer from ever reaching
-- the engine at all. Cleared on reload, which is correct: ids do not outlive a
-- session.
local issued_ids = {}

--: WE made the current pause. Same lifetime and same reset as `issued_ids`: a module
--: local, so a UI reload (alt-enter, loading a save) re-creates it as false. The pause
--: itself SURVIVES that reload engine-side, and the new chunk cannot prove it made it,
--: so `unpause` then refuses and the pause is undone in game. Deliberate: owning a
--: pause by inference from "the game is paused" would let this channel undo the
--: player's own pause, which is the failure ownership exists to prevent. Vanilla keeps
--: the same discipline (gamemodified.lua sets menu.paused before Pause() and calls
--: Unpause() only if it is set).
--:
--: KNOWN GAPS, stated rather than hidden -- the engine exposes no pause identity:
--:  * a pause we made, undone by the player and re-made by the player BETWEEN two of
--:    our calls, is indistinguishable from ours. A stale flag is cleared whenever a
--:    write verb SEES the game unpaused.
--:  * a vanilla MENU that pauses while ours holds (options, ship configuration) calls
--:    Pause()/Unpause() without checking. Whether the engine COUNTS pauses is
--:    UNMEASURED. If it does not, our `unpause` resumes the game under an open menu and
--:    nothing here can see it. If it does, our `unpause` reads back paused, and the
--:    claim is released rather than retried -- see pause_write.
local our_pause = false

--: The wire form of a UniverseID is `tostring(cdata)` VERBATIM -- LuaJIT renders it
--: with a ULL suffix, and vanilla's own round trip is
--: `ConvertStringTo64Bit(tostring(id))` (553 bare uses). Treat it as an OPAQUE
--: string on both sides: never parse it, never int() it in Python. A uint64 above
--: 2^53 does not survive a double.
--:
--: ★ THERE ARE TWO ID REPRESENTATIONS AND THEY STRINGIFY DIFFERENTLY. MEASURED in
--: game 2026-08-30:
--:     tostring(C.GetPlayerOccupiedShipID())    = 1046331ULL   ffi cdata
--:     tostring(ConvertIDTo64Bit(<luaid>))      = 282463       lua number
--:     tostring(ConvertStringTo64Bit("282463")) = 282463       lua number
--: Same uint64 to the engine; two different strings on our side. The ffi verbs issue
--: the first form, and `GetContainedObjectsByOwner` hands back LuaIDs that normalise
--: to the second -- so without a single choke point the SAME OBJECT would get TWO
--: ids, which breaks dedupe and makes any set comparison between two enumeration
--: paths meaningless. (The two lines above are different objects, so the difference
--: is INFERRED, not proven; `compare` renders one object both ways and settles it.
--: This is written so that it does not matter: if they never differ, `wire` is a
--: no-op.)
local function wire(v)
    --: ALREADY CANONICAL? Decided by what the value RENDERS AS, not by its lua type.
    --: A `uint64` cdata stringifies to exactly this, so the ffi path needs no special
    --: case -- which also means the branch is reachable offline. Keying it on
    --: `type(v) == "cdata"` instead made it untestable in the harness, and a branch no
    --: test can reach is a branch nothing defends.
    --: A LuaID renders as `ID: 282463`, so it can never collide with this pattern.
    local s = tostring(v)
    if s:match("^%d+ULL$") then return s end

    local n
    local t = type(v)
    if t == "number" then n = v
    elseif t == "string" then n = ConvertStringTo64Bit(v)
    else n = ConvertIDTo64Bit(v) end     -- a LuaID, or whatever else the engine hands us
    if type(n) ~= "number" then return tostring(ffi.cast("UniverseID", n)) end
    --: ENFORCE the 2^53 rule rather than remembering it. A lua number is a double, so
    --: above 2^53 it silently stops being able to hold a distinct uint64 -- and the
    --: bare engine globals return lua numbers. Observed ids are ~10^7 (33556742), so
    --: there is a large margin today; this is the check that notices if that changes.
    if n >= 9007199254740992 then
        error("id " .. string.format("%.0f", n) .. " is at or above 2^53 and cannot be "
              .. "represented exactly as a lua number; the bare-global conversion path "
              .. "is no longer safe for this save")
    end
    return tostring(ffi.cast("UniverseID", n))
end

--: For OBJECT ids. Canonicalises, so every verb emits one string per object.
local function issue(v)
    local s = wire(v)
    -- A reported null handle is absence, never permission to inspect object zero.
    if s ~= "0ULL" then issued_ids[s] = true end
    return s
end

--: For SECTOR tokens, which are a DIFFERENT KIND and must not be canonicalised.
--: `GetComponentData(id, "sectorid")` returns `ID: 7479`, vanilla feeds exactly that
--: to `ConvertStringToLuaID(tostring(...))` (menu_diplomacy.lua:1925), and it is what
--: `stations`/`ships`/`objects` take as their sector argument. Running it through
--: `wire` would rewrite the token `player` hands out and silently break every
--: sector-scoped query. ⚠ Sector tokens are NOT stable across launches (MEASURED:
--: ID: 4305 then ID: 7479 for the same sector) -- never persist one.
local function issue_token(s)
    issued_ids[s] = true
    return s
end


local verbs = {}

--: THE WRITE VOCABULARY, in its own table so that "is this verb a write?" is answered
--: by WHICH TABLE holds it, never by reading its name. Rules, each pinned by a test in
--: tests/test_modlua_rearm.py:
--:   1. HARDCODED and ARGUMENT-FREE. Nothing a caller sends can change what a write
--:      does, so there is no argument surface to validate and none to get wrong.
--:   2. ATTESTED: each engine call is one vanilla makes, in the shape vanilla makes it
--:      (tests/test_recon_buckets_are_vanilla_attested.py checks Pause/Unpause bare).
--:   3. READ BACK, never intention: the reply reports what the engine says afterwards.
--:   4. ADVISORY FIRST: every reply leads with a `write=yes ...` row. reply() enforces
--:      it once, so a write verb that forgets cannot ship an unmarked frame.
--:   5. A name may not sit in both tables; the dispatcher refuses rather than guess.
local WRITE_VERBS = {}

-- ping also carries getElapsedTime(). ⚠ That is REAL time, not game time: MEASURED
-- 2026-09-13, it kept advancing across a paused game (76.45 -> 84.81 over 8.35 s of
-- wall clock, most of it paused), so two identical readings do NOT mean paused. Ask
-- `pausestate` for that. Collapsing paused/hung/not-loaded into one verdict is the bug
-- that made a sibling project's liveness probe wrong for three releases.
verbs.ping = function(seq)
    local t = "unknown"
    if type(getElapsedTime) == "function" then
        local ok, v = pcall(getElapsedTime)
        if ok then t = tostring(v) end
    end
    reply(seq, "OK", table.concat({ "pong", tostring(PROTO), t }, "\t"))
end

-- The CAPABILITY PROBE, and the reason it ships BEFORE the verbs that need it.
--
-- Our lua loads into the shared `menus` environment (ui.xml declares
-- environment type="menus"). MEASURED across vanilla: all 25 files that call
-- GetComponentData are menus-registered, and the one file that is NOT
-- (ui/core/lua/monitors.lua) had to reach IsValidComponent through ffi.cdef
-- instead -- the only cdef of that symbol in the tree. So reachability from OUR
-- file is a strong inference, not a measurement, and the answer decides whether
-- the live view is written against bare globals or a private cdef block. Getting
-- that wrong costs a full build against the wrong shape.
--
-- It reports TYPES ONLY and never invokes a symbol, so no argument can be wrong
-- and there is nothing here that can crash the game.
local PROBE_SYMBOLS = {
    "GetComponentData", "GetComponentClass", "IsValidComponent",
    "GetPlayerOccupiedShipID", "GetPlayerID",
    "GetNumAllFactionStations", "GetAllFactionStations",
    "GetNumAllFactionShips", "GetAllFactionShips",
    "GetObjectPositionInSector",
    "ConvertStringTo64Bit", "ConvertIDTo64Bit",
    "GetMacroData", "GetLibraryEntry",
    --: Not called by any verb. ⚠ This entry USED to say it was "needed by Init to reset
    --: the id allowlist on a save load", and that reading -- present therefore working --
    --: is exactly what hid a dead hook for weeks. MEASURED 2026-08-29: RegisterEvent is
    --: PRESENT and the handler Init registers with it has NEVER FIRED. Presence of a
    --: symbol says nothing about a callback running. What actually clears the allowlist
    --: is chunk re-creation; see Init. Watch `load_hook_fired` below, not this line.
    "RegisterEvent",
    --: Candidates for a cross-faction `objects <sector>` verb, probed BEFORE anything is
    --: built on them. `GetContainedObjectsByOwner(owner [, container])` is a bare engine
    --: global (6 vanilla call sites, no lua definition anywhere in ui/) that returns a
    --: PLAIN LUA TABLE and takes an optional container -- so it could push the sector
    --: filter engine-side, where today `enumerate` walks the whole galaxy per faction and
    --: discards the rest in lua. ⚠ 0 of those 6 call sites pass a SECTOR as the
    --: container, so that specific use is unverified and this probe is how we find out
    --: rather than discovering it from a broken verb.
    "GetContainedObjectsByOwner",
    --: The faction list, so `objects` can merge across factions instead of taking one.
    --: cdef'd in vanilla (customgame.lua:279/:224) rather than called bare, so a bare
    --: lookup may well read nil here -- which is itself the answer, and why both the env
    --: and C forms are checked.
    "GetNumAllFactions", "GetAllFactions",
}

verbs.probe = function(seq)
    -- TWO lookups per symbol, because they can disagree. A bare reference resolves
    -- through this chunk's ENVIRONMENT; _G is the global table. If the engine
    -- installs its bindings anywhere other than plain _G the two differ, and a
    -- probe checking only one would report a confident absence for a symbol that is
    -- reachable. The existing verbs prove the bare form works (GetLibraryEntry is
    -- reached that way and returns real data), so env is the authority and _G is
    -- the cross-check -- and a disagreement is itself the finding.
    local env = (type(getfenv) == "function") and getfenv(1) or _G
    local out = {}
    for i = 1, #PROBE_SYMBOLS do
        local name = PROBE_SYMBOLS[i]
        local via_env, via_g = type(env[name]), type(_G[name])
        if via_env == via_g then
            out[#out + 1] = name .. "=" .. via_env
        else
            out[#out + 1] = name .. "=env:" .. via_env .. ",_G:" .. via_g
        end
    end
    -- Reached differently from the rest: ffi is a module, Helper a bare table.
    -- Both matter. If GetComponentData turns out to be absent we need ffi; and the
    -- faction-enumeration pair is cdef-only in vanilla either way.
    -- NB deliberately NOT named ffi_ok: that is a module-level local holding the real
    -- state of our cdef block, and shadowing it here would make the probe report on
    -- its own throwaway variable instead of on the thing being probed.
    local can_require_ffi = pcall(require, "ffi")
    out[#out + 1] = "require_ffi=" .. tostring(can_require_ffi)
    out[#out + 1] = "Helper=" .. type(Helper)
    out[#out + 1] = "Helper.ffiVLA="
                    .. (type(Helper) == "table" and type(Helper.ffiVLA) or "n/a")

    -- Our own FFI block, which is what the ffi-only verbs actually depend on. A bare
    -- global reading nil is expected for these; what matters is whether `C.<name>`
    -- resolves after our cdef.
    -- FIRST, because it is what decides whether anything else in this reply is worth
    -- reading: it identifies the build the GAME is running, which is not necessarily
    -- the build on disk.
    out[#out + 1] = "build=" .. BUILD
    out[#out + 1] = "equipment_protocol=" .. (verbs.equipment and "1" or "unavailable")
    --: Reload bookkeeping. `build=` answers "is the game running the file on disk";
    --: these answer "has the chunk been re-executed since I last asked", which is a
    --: DIFFERENT question and the one that explains a rejected id. A reload keeps the
    --: same build and still empties the allowlist.
    out[#out + 1] = "loaded_at=" .. string.format("%.2f", LOADED_AT)
    out[#out + 1] = "load_hook_fired=" .. tostring(load_hook_fired)
    --: F124: is THIS helper's pipe protected from the Mod Support APIs unref defect?
    --: `active` = gate passed and our open file(s) stamped; `armed` = wrapper installed
    --: but no own file seen yet; `inactive:<reason>` = nothing written, churn unsafe.
    --: A `;wrapper-replaced` suffix: Connect_Pipe is no longer our wrapper. Stamping goes
    --: on whenever our wrapper is still called (another mod wrapped it); watch the count.
    if pipe_guard then
        out[#out + 1] = "pipe_unref_guard=" .. pipe_guard.status()
        out[#out + 1] = "pipe_unref_stamped=" .. tostring(pipe_guard.stamped())
    else
        out[#out + 1] = "pipe_unref_guard=inactive:the named pipes api did not load"
        out[#out + 1] = "pipe_unref_stamped=0"
    end
    out[#out + 1] = "our_ffi=" .. tostring(ffi_ok) .. ",cdef=" .. tostring(cdef_ok)
    if cdef_ok then
        for _, name in ipairs({ "GetPlayerOccupiedShipID", "GetNumAllFactionStations",
                                "GetComponentClass", "GetObjectPositionInSector",
                                "GetNumAllFactions", "GetAllFactions",
                                "GetNumUpgradeSlots", "GetUpgradeSlotGroup",
                                "GetUpgradeSlotCurrentComponent", "GetUpgradeSlotCurrentMacro",
                                "GetNumUpgradeGroups", "GetUpgradeGroups2", "GetUpgradeGroupInfo2" }) do
            -- Indexing ffi.C for a symbol the engine does not export RAISES rather
            -- than returning nil, so this has to be pcall'd to be a probe at all.
            local rok, sym = pcall(function() return C[name] end)
            out[#out + 1] = "C." .. name .. "=" .. (rok and type(sym) or "<not exported>")
        end
    end

    --: ★ LIVE CALL PROBE -- because a symbol's TYPE does not tell you whether a given
    --: ARGUMENT SHAPE works, and that is the actual open question for `objects`.
    --: Two calls, reported SEPARATELY so a failure names which one failed:
    --:   owner_only  does the function work at all from our namespace?
    --:   with_sector does it accept a SECTOR as the container? 0 of 6 vanilla call
    --:               sites pass one (they pass a station or ship), so this is genuinely
    --:               unknown and is the clause the design turns on.
    --:
    --: ⚠ THE OWNER IS "argon", NOT "player", AND THAT IS THE WHOLE POINT. The first
    --: version of this probe asked about the PLAYER and reported `table:1` for both
    --: calls -- because the player owns one ship. "correctly filtered to 1" and
    --: "silently ignored the container argument" produce IDENTICAL output there, so it
    --: could not answer its own question. A faction owning objects across many sectors
    --: makes the two numbers diverge: all >> in_sector means the container is honoured,
    --: all == in_sector means it is being discarded (or, implausibly, that this faction
    --: is confined to one sector -- so read the numbers, do not just compare them).
    --: The counts are REPORTED rather than judged here: the discrimination belongs to
    --: whoever reads it, with both denominators in front of them.
    --: The sector handle is built the way VANILLA builds it -- menu_map.lua:13770 does
    --: `ConvertStringTo64Bit(tostring(sectorid))` on the very field GetComponentData
    --: returns -- rather than by assuming the raw "ID: nnnn" string is a handle.
    --: Everything is pcall'd: a probe that can raise is not a probe.
    if type(GetContainedObjectsByOwner) == "function" then
        local function shape(v)
            if type(v) ~= "table" then return type(v) end
            local n = 0
            for _ in pairs(v) do n = n + 1 end
            return "table:" .. tostring(n)
        end
        local PROBE_FACTION = "argon"
        local ok1, r1 = pcall(GetContainedObjectsByOwner, PROBE_FACTION)
        out[#out + 1] = "contained_all_" .. PROBE_FACTION .. "="
                        .. (ok1 and shape(r1) or ("raised:" .. tostring(r1)))
        if cdef_ok then
            local no_occupied_ship = false
            local ok2, r2 = pcall(function()
                local raw = C.GetPlayerOccupiedShipID()
                if wire(raw) == "0ULL" then no_occupied_ship = true; return nil end
                local ship = ConvertStringTo64Bit(tostring(raw))
                local sect = GetComponentData(ship, "sectorid")
                local sector64 = ConvertStringTo64Bit(tostring(sect))
                return GetContainedObjectsByOwner(PROBE_FACTION, sector64)
            end)
            out[#out + 1] = "contained_in_my_sector="
                            .. (ok2 and (no_occupied_ship and "<unavailable:no occupied ship>" or shape(r2))
                                or ("raised:" .. tostring(r2)))
        end
    else
        out[#out + 1] = "contained_all_argon=<symbol absent>"
    end

    reply(seq, "OK", table.concat(out, "\t"))
end

-- --------------------------------------------------------------------------- --
-- STEP-0 INSTRUMENT for the `objects` verb
-- --------------------------------------------------------------------------- --
--
--: A MEASUREMENT, not a feature. It exists to answer three questions BEFORE any of
--: `objects` is written, because all three touch the id contract every other verb
--: rests on -- and a wrong guess there is found the hard way, in game, with a broken
--: `component`. Confidence on question 1 was 70% when this was written; the standing
--: rule is that sub-90% buys a measurement rather than a gate.
--:
--:   1 WIRE FORM (the load-bearing one). GetContainedObjectsByOwner returns LuaIDs,
--:     NOT UniverseIDs -- vanilla normalises every one with ConvertIDTo64Bit
--:     (menu_map.lua:8229, menu_playerinfo.lua:1227, helper.lua:11154), and
--:     helper.lua:1893-97 shows the two are genuinely distinct representations. Our
--:     entire allowlist/`component` contract is `tostring(UniverseID cdata)`. If the
--:     normalised form does not round-trip identically, `objects` cannot issue ids at
--:     all without breaking every id-consuming verb. No cdef for ConvertIDTo64Bit
--:     exists anywhere in reference\ui, so its return type is UNVERIFIED -- this is
--:     the only way to find out short of shipping it.
--:   2 CONTAINER HANDLE. An earlier probe measured the sector container working with
--:     ConvertStringTo64Bit (argon: 2028 galaxy-wide vs 267 in sector). But vanilla
--:     builds it with ConvertStringToLuaID -- menu_platformundock.lua:121, and
--:     menu_diplomacy.lua:1925 which passes a SECTOR exactly as we intend to. Both
--:     forms are called here and the counts compared: agreement settles it, and a
--:     DISAGREEMENT is the more valuable finding.
--:   3 CLASS FILTER. Helper.isComponentClass(classid, "ship") and
--:     (realclassid, "station") -- note the DIFFERENT field per class, which is
--:     vanilla's own idiom at menu_map.lua:7371 and :7875 and is not something you
--:     would compose from the schema. Both fields are already in DEFAULT_FIELDS, so
--:     the filter costs zero extra engine calls. Cross-checked two ways: against bare
--:     IsComponentClass (28 vanilla uses), and against GetContainedStationsByOwner,
--:     which is an INDEPENDENT engine-side count of the same set and therefore a
--:     control that can actually go red.
--:
--: Everything is pcall'd and every failure is REPORTED rather than raised. A probe
--: that can raise is not a probe, and a probe that omits what it could not read is
--: an absence indistinguishable from a non-answer.
verbs.containerprobe = function(seq, faction)
    if type(GetContainedObjectsByOwner) ~= "function" then
        reply(seq, "ERR", "GetContainedObjectsByOwner is absent; nothing to probe")
        return
    end
    if not cdef_ok then
        reply(seq, "ERR", "containerprobe needs the ffi cdef block, which did not load")
        return
    end
    -- argon by default for the same reason the earlier probe used it: a faction
    -- spanning many sectors makes "filtered" and "ignored" produce different numbers.
    -- The player owns too little for the branches to diverge, which is exactly how an
    -- earlier version of this measurement reported table:1 against table:1 and could
    -- not answer its own question.
    faction = (faction ~= nil and faction ~= "") and faction or "argon"

    local out = {}
    local function put(k, v) out[#out + 1] = k .. "=" .. tostring(v) end
    local function count(v)
        if type(v) ~= "table" then return type(v) end
        local n = 0
        for _ in pairs(v) do n = n + 1 end
        return n
    end

    put("faction", faction)

    -- Reachability first: a later failure is uninterpretable without knowing whether
    -- the symbol was even there. env is the authority and _G the cross-check, as in
    -- `probe` -- a disagreement between them is itself a finding.
    local env = (type(getfenv) == "function") and getfenv(1) or _G
    for _, n in ipairs({ "ConvertIDTo64Bit", "ConvertStringToLuaID",
                         "GetContainedStationsByOwner", "IsComponentClass",
                         "IsValidComponent", "Helper" }) do
        local a, b = type(env[n]), type(_G[n])
        put("sym." .. n, (a == b) and a or ("env:" .. a .. ",_G:" .. b))
    end
    put("sym.Helper.isComponentClass",
        (type(Helper) == "table") and type(Helper.isComponentClass) or "n/a")

    local okship, ship = pcall(function()
        return ConvertStringTo64Bit(tostring(C.GetPlayerOccupiedShipID()))
    end)
    if not okship then
        reply(seq, "ERR", "could not read the player ship: " .. tostring(ship))
        return
    end
    if wire(ship) == "0ULL" then
        reply(seq, "ABSENT", "no occupied ship; container probe needs a ship sector")
        return
    end
    local oksid, sid = pcall(GetComponentData, ship, "sectorid")
    if not oksid or sid == nil then
        reply(seq, "ERR", "could not read the player sectorid: " .. tostring(sid))
        return
    end
    put("sectorid", tostring(sid))

    -- Q2. Three calls, so the two container forms are each compared against the SAME
    -- galaxy-wide denominator rather than against each other alone.
    local okall, lall = pcall(GetContainedObjectsByOwner, faction)
    put("n.owner_only", okall and count(lall) or ("raised:" .. tostring(lall)))
    local ok64, l64 = pcall(function()
        return GetContainedObjectsByOwner(faction, ConvertStringTo64Bit(tostring(sid)))
    end)
    put("n.sector_via_64bit", ok64 and count(l64) or ("raised:" .. tostring(l64)))
    local oklua, llua = pcall(function()
        return GetContainedObjectsByOwner(faction, ConvertStringToLuaID(tostring(sid)))
    end)
    put("n.sector_via_luaid", oklua and count(llua) or ("raised:" .. tostring(llua)))

    -- Prefer vanilla's form when it works; fall back to the one already measured. Say
    -- WHICH was used, because every number below depends on it and a reader cannot
    -- infer it from the counts.
    local list, used
    if oklua and type(llua) == "table" then list, used = llua, "luaid"
    elseif ok64 and type(l64) == "table" then list, used = l64, "64bit"
    else used = "<neither>" end
    put("used_handle", used)

    -- Q1. THE contract question. Reported as raw strings, not as a verdict: the two
    -- 64-bit forms must be eyeball-comparable, and `roundtrip_isvalid` is the thing
    -- `component <id>` will actually do with whatever we put on the wire.
    if list ~= nil and list[1] ~= nil then
        local obj = list[1]
        put("wire.raw_luaid", tostring(obj))
        put("wire.reference_ffi", tostring(C.GetPlayerOccupiedShipID()))
        local okc, id64 = pcall(ConvertIDTo64Bit, obj)
        put("wire.via_ConvertIDTo64Bit", okc and tostring(id64) or ("raised:" .. tostring(id64)))
        if okc then
            local okr, back = pcall(ConvertStringTo64Bit, tostring(id64))
            put("wire.roundtrip", okr and tostring(back) or ("raised:" .. tostring(back)))
            put("wire.roundtrip_identical", okr and (tostring(back) == tostring(id64)))
            if okr then
                local okv, valid = pcall(IsValidComponent, back)
                put("wire.roundtrip_isvalid", okv and tostring(valid) or ("raised:" .. tostring(valid)))
                local okn, nm = pcall(GetComponentData, back, "name")
                put("wire.roundtrip_name", okn and tostring(nm) or ("raised:" .. tostring(nm)))
            end
        end
    else
        put("wire", "<no objects here for " .. faction .. "; re-run with a faction that has some>")
    end

    -- Q3. Two independent discriminators over the SAME list, plus one independent
    -- engine call over the same question. Three numbers that must agree.
    if list ~= nil and #list > MAX_ENUMERATE then
        -- The same bound, a different shape: this is ONE block of a probe, and
        -- the symbol reachability, the wire round-trip and Q2's three counts
        -- above are cheap and already answered. So the WALK is declined, not the
        -- verb. MEASURED offline at N=5,000: 25,012 engine calls in one frame,
        -- 5 per object.
        --
        -- ⚠ And not capped, for the reason class.stations_engine_control
        -- exists: that control is an INDEPENDENT engine count over the WHOLE
        -- list, so a truncated walk would disagree with it by construction and
        -- the block would report a class-discrimination defect that is really
        -- just the cap. A control you have quietly narrowed is worse than none.
        put("class.SKIPPED", "n=" .. #list .. " over limit=" .. MAX_ENUMERATE
            .. "; walking it is ~5 engine calls per object in ONE frame, and a"
            .. " partial walk would disagree with class.stations_engine_control"
            .. " purely because it is partial. Re-run against a smaller faction.")
    elseif list ~= nil then
        local nsh_h, nst_h, nsh_b, nst_b, rows = 0, 0, 0, 0, 0
        local helper_ok, bare_ok = true, true
        for _, obj in ipairs(list) do
            rows = rows + 1
            local okd, cid, rcid = pcall(GetComponentData, obj, "classid", "realclassid")
            if okd then
                local o1, isship = pcall(function() return Helper.isComponentClass(cid, "ship") end)
                local o2, isstat = pcall(function() return Helper.isComponentClass(rcid, "station") end)
                if o1 and isship then nsh_h = nsh_h + 1 end
                if o2 and isstat then nst_h = nst_h + 1 end
                if not (o1 and o2) then helper_ok = false end
            else
                helper_ok = false
            end
            local o3, s3 = pcall(function() return IsComponentClass(obj, "ship") end)
            local o4, s4 = pcall(function() return IsComponentClass(obj, "station") end)
            if o3 and s3 then nsh_b = nsh_b + 1 end
            if o4 and s4 then nst_b = nst_b + 1 end
            if not (o3 and o4) then bare_ok = false end
        end
        put("class.rows_walked", rows)
        put("class.helper_usable", helper_ok)
        put("class.bare_usable", bare_ok)
        put("class.ships_helper", nsh_h)
        put("class.ships_bare", nsh_b)
        put("class.stations_helper", nst_h)
        put("class.stations_bare", nst_b)
    end
    -- The independent control. menu_diplomacy.lua:1925 calls it in exactly this shape,
    -- sector container included. If this disagrees with class.stations_*, one of the
    -- two readings of "station" is wrong and the migration is not ready.
    if type(GetContainedStationsByOwner) == "function" then
        local oks, ls = pcall(function()
            return GetContainedStationsByOwner(faction, ConvertStringToLuaID(tostring(sid)))
        end)
        put("class.stations_engine_control", oks and count(ls) or ("raised:" .. tostring(ls)))
    else
        put("class.stations_engine_control", "<symbol absent>")
    end

    -- Q5 from the plan: is the faction list reachable at all? `objects` with no
    -- faction argument is built on this, and an unexported symbol changes the design.
    for _, n in ipairs({ "GetNumAllFactions", "GetAllFactions" }) do
        local rok, sym = pcall(function() return C[n] end)
        put("C." .. n, rok and type(sym) or "<not exported>")
    end
    -- NB an explicit if, not `okn and nfac or ...`: when the call SUCCEEDS but yields
    -- nil, the and/or form reports "raised:nil" and blames an error that never
    -- happened. A diagnostic that misattributes its own failure is worse than none.
    local okn, nfac = pcall(function() return tonumber(C.GetNumAllFactions(false)) end)
    if not okn then put("n.factions", "raised:" .. tostring(nfac))
    elseif nfac == nil then put("n.factions", "<call ok but not a number>")
    else put("n.factions", nfac) end

    reply(seq, "OK", table.concat(out, "\t"))
end

-- The RAMP INSTRUMENT for the unknown message-size cap. Returns exactly n payload
-- bytes; the Python side ramps n and records where the reply first comes back short.
verbs.echo = function(seq, n)
    local count = tonumber(n)
    if count == nil then reply(seq, "ERR", "echo needs a byte count") return end
    count = math.floor(count)
    if count < 0 then count = 0 end
    if count > ECHO_MAX then
        reply(seq, "ERR", "requested " .. count .. " exceeds ECHO_MAX " .. ECHO_MAX)
        return
    end
    -- UNBOUNDED on purpose. This verb exists to be too big; see reply().
    reply(seq, "OK", string.rep("x", count), true)
end

verbs.errors = function(seq)
    if type(GetNumErrors) ~= "function" then
        reply(seq, "ERR", "GetNumErrors is not a function in this environment")
        return
    end
    local ok, n = pcall(GetNumErrors)
    if not ok then reply(seq, "ERR", "GetNumErrors failed: " .. tostring(n)) return end
    -- A count of zero is reported as OK 0. "none" must never wear the grammar of
    -- "cannot ask" -- that is what the ERR branch above is for.
    reply(seq, "OK", tostring(n))
end

verbs.ext = function(seq, id)
    if id == nil or id == "" then reply(seq, "ERR", "ext needs an extension id") return end
    if type(GetExtensionList) ~= "function" then
        reply(seq, "ERR", "GetExtensionList is not a function in this environment")
        return
    end
    local ok, list = pcall(GetExtensionList)
    if not ok then reply(seq, "ERR", "GetExtensionList failed: " .. tostring(list)) return end
    if type(list) ~= "table" then reply(seq, "ERR", "GetExtensionList returned " .. type(list)) return end
    for _, e in ipairs(list) do
        if type(e) == "table" and tostring(e.id) == id then
            -- Every field, k=v, rather than a hand-picked subset: we do not have to
            -- guess the vocabulary, and a field the engine adds later shows up
            -- instead of being silently dropped.
            local out, used, shown, total = {}, 0, 0, 0
            for _, k in ipairs(sorted_keys(e)) do
                local v = e[k]
                total = total + 1
                local fld = k .. "=" .. (type(v) == "table" and "<table>" or tostring(v))
                -- Bounded BEFORE sending, and no break: stopping the loop would stop
                -- counting too, and then `total` would just be `shown` again --
                -- destroying the honest denominator (the same reasoning as the
                -- station enumerator).
                if used + #fld + 1 <= ROW_BUDGET then
                    out[#out + 1] = fld
                    used = used + #fld + 1
                    shown = shown + 1
                end
            end
            -- Appended ONLY when something was omitted, so an ordinary reply is
            -- byte-identical to before this cap existed.
            if shown < total then
                out[#out + 1] = "__shown=" .. shown
                out[#out + 1] = "__total=" .. total
            end
            reply(seq, "OK", table.concat(out, "\t"))
            return
        end
    end
    -- ABSENT is an ANSWER: the engine was asked and this id is not in its list.
    reply(seq, "ABSENT", id)
end

verbs.macro = function(seq, ltype, name, ...)
    -- `--contents` is a FLAG, taken out before the positional <property>. Left in place it
    -- would sit in the property slot and be looked up as a field literally named
    -- `--contents`, answering ABSENT for an entry that exists.
    local prop, contents = nil, false
    for _, a in ipairs({ ... }) do
        if a == "--contents" then contents = true
        elseif prop == nil and a ~= "" then prop = a end
    end
    if ltype == nil or ltype == "" or name == nil or name == "" then
        reply(seq, "ERR", "macro needs <librarytype> <macroname> [<property>] [--contents]")
        return
    end
    if type(GetLibraryEntry) ~= "function" then
        reply(seq, "ERR", "GetLibraryEntry is not a function in this environment")
        return
    end
    local ok, entry = pcall(GetLibraryEntry, ltype, name)
    -- ERR, not ABSENT, and the message is KEPT. On a pcall failure `entry` holds
    -- the error text, and reporting that as ABSENT says "the engine has nothing
    -- here" when the truth is "we could not ask" -- the conflation this file's own
    -- header forbids. It also reconciled silently: _livecli counts ABSENT as a real
    -- answer, so a harvest that lost N answers this way still balanced, and the
    -- result becomes a groundtruth fixture. verbs.ext already does this correctly.
    if not ok then reply(seq, "ERR", "GetLibraryEntry failed: " .. tostring(entry)) return end
    -- Every ABSENT below is a GENUINE absence: we asked, and there is nothing there.
    if type(entry) ~= "table" then reply(seq, "ABSENT", ltype .. "\t" .. name) return end

    local ks = sorted_keys(entry)
    -- An EMPTY table is an absence, not an answer. Whether GetLibraryEntry raises or
    -- returns {} for an unknown name is not documented, so both are handled --
    -- reporting {} as "OK, no fields" is exactly the narrowing-step-reports-success
    -- shape this workspace keeps finding.
    if #ks == 0 then reply(seq, "ABSENT", ltype .. "\t" .. name) return end

    if prop ~= nil and prop ~= "" then
        local v = entry[prop]
        if v == nil then
            reply(seq, "ABSENT", ltype .. "\t" .. name .. "\t" .. prop)
        else
            reply(seq, "OK", tostring(v))
        end
        return
    end

    local out, used, shown, total = {}, 0, 0, 0
    for _, k in ipairs(ks) do
        local v = entry[k]
        total = total + 1
        -- A table value is `<table>` unless `--contents` asked for two levels of it: the
        -- default must stay byte-identical, because a harvest stores this payload as its
        -- `*` row and the oracle reads it.
        local fld = k .. "=" .. (type(v) == "table"
                                 and (contents and render_table(v, 2) or "<table>")
                                 or tostring(v))
        if used + #fld + 1 <= ROW_BUDGET then
            out[#out + 1] = fld
            used = used + #fld + 1
            shown = shown + 1
        end
    end
    if shown < total then
        out[#out + 1] = "__shown=" .. shown
        out[#out + 1] = "__total=" .. total
    end
    reply(seq, "OK", table.concat(out, "\t"))
end

-- --------------------------------------------------------------------------- --
-- THE LIVE VIEW -- where am I, and what is out there
-- --------------------------------------------------------------------------- --

--: Read when `component` is called with no explicit field list. Every name here is
--: a literal vanilla passes to GetComponentData; `menu_map.lua:8952` uses this exact
--: shape in one call. NB "class" is NOT a field -- class-as-string comes from
--: ffi.string(C.GetComponentClass(id)), which is a different namespace entirely.
local DEFAULT_FIELDS = {
    "name", "sector", "sectorid", "classid", "realclassid",
    "owner", "ownername", "macro", "isdocked",
}

--: Shared by `player` and the enumerators. Returns a tab-joined "k=v" list.
local function read_fields(id, names)
    local out = {}
    for i = 1, #names do
        local ok, v = pcall(GetComponentData, id, names[i])
        out[#out + 1] = names[i] .. "=" .. (ok and tostring(v) or "<read failed>")
    end
    return out
end

-- Where the player is. Seeds the id allowlist, so it is the entry point for
-- everything else -- `component` cannot be used until something has issued an id.
verbs.player = function(seq)
    if not cdef_ok then
        reply(seq, "ERR", "player needs the ffi cdef block, which did not load")
        return
    end
    local ok, res = pcall(function()
        local ship_raw = C.GetPlayerOccupiedShipID()
        -- The cdata goes to `issue` directly rather than pre-stringified: `wire` is
        -- the ONE place that decides what an id looks like on the pipe.
        local ship_str = wire(ship_raw)

        local out = { "occupiedship=" .. ship_str }
        -- The player ENTITY is a separate id from the ship they are sitting in, and
        -- conflating them is an easy way to inspect the wrong object.
        local pok, p_raw = pcall(C.GetPlayerID)
        if pok then out[#out + 1] = "playerid=" .. issue(p_raw) end

        out[#out + 1] = "occupiedship_present=" .. tostring(ship_str ~= "0ULL")
        if ship_str == "0ULL" then return out end
        issue(ship_raw)
        local ship_id = ConvertStringTo64Bit(ship_str)

        for _, kv in ipairs(read_fields(ship_id, DEFAULT_FIELDS)) do
            out[#out + 1] = kv
        end
        -- The sector id is issued too: it is what `stations <faction> <sectorid>`
        -- takes, which is how "what is around ME" is actually answered.
        local sok, sect = pcall(GetComponentData, ship_id, "sectorid")
        -- issue_TOKEN: a sector token is a different kind from an object id and must
        -- reach the caller byte-for-byte as the engine wrote it. See issue_token.
        if sok and sect ~= nil then issue_token(tostring(sect)) end

        -- Position is NOT a GetComponentData field (MEASURED: zero such literals in
        -- vanilla); it is an ffi struct return, which is why the cdef carries UIPosRot.
        local ppok, pos = pcall(C.GetObjectPositionInSector, ship_id)
        if ppok and pos ~= nil then
            out[#out + 1] = string.format("pos=%.1f,%.1f,%.1f", pos.x, pos.y, pos.z)
            out[#out + 1] = string.format("rot=%.3f,%.3f,%.3f", pos.yaw, pos.pitch, pos.roll)
        end
        local cok2, cls = pcall(C.GetComponentClass, ship_id)
        if cok2 and cls ~= nil then out[#out + 1] = "class=" .. ffi.string(cls) end
        return out
    end)
    if not ok then reply(seq, "ERR", "player raised: " .. tostring(res)) return end
    reply(seq, "OK", table.concat(res, "\t"))
end

-- The general inspector -- the DevBench surface. Fields are supplied by the CALLER,
-- so the vocabulary stops being something this mod has to predict.
verbs.component = function(seq, id_str, ...)
    if id_str == nil or id_str == "" then
        reply(seq, "ERR", "component needs an id; get one from player, stations or ships")
        return
    end
    if not issued_ids[id_str] then
        --: TWO very different causes produce this one refusal, and they need different
        --: actions from the caller, so name the likelier one instead of making them
        --: guess. MEASURED 2026-08-29: a UI reload -- pressing ALT-ENTER, or loading a
        --: save -- re-executes this chunk and empties the allowlist, so an id that
        --: worked seconds earlier stops resolving with no other symptom. Until this
        --: message existed that was INDISTINGUISHABLE from a fabricated id, and the
        --: refusal pointed at the caller when the cause was a reload.
        local n = 0
        for _ in pairs(issued_ids) do n = n + 1 end
        local why
        if n == 0 then
            why = "NOTHING has been issued by this chunk (loaded at "
                  .. string.format("%.2f", LOADED_AT)
                  .. "), which is exactly what a UI RELOAD looks like: alt-enter or a save "
                  .. "load re-executes this file and empties the allowlist. Re-run player, "
                  .. "stations or ships and use a fresh id."
        else
            why = tostring(n) .. " id(s) are issued by this chunk (loaded at "
                  .. string.format("%.2f", LOADED_AT)
                  .. ") and this is not one of them, so it is most likely mistyped, or left "
                  .. "over from before a UI reload."
        end
        reply(seq, "ERR", "id was not issued by this session, so it will not be sent to the "
              .. "engine. " .. why .. " MEASURED: 0 of 888 vanilla GetComponentData calls pass "
              .. "a foreign id, so the engine's behaviour on one is UNKNOWN in both directions.")
        return
    end
    local ok, id = pcall(ConvertStringTo64Bit, id_str)
    if not ok or id == nil then
        reply(seq, "ERR", "could not convert id " .. tostring(id_str) .. " to a 64-bit handle")
        return
    end
    -- Handles go stale between frames -- vanilla guards this way on values the engine
    -- itself returned, which is exactly our case one frame later.
    if type(IsValidComponent) == "function" then
        local vok, valid = pcall(IsValidComponent, id)
        if not vok then reply(seq, "ERR", "IsValidComponent raised on that id") return end
        if not valid then
            -- ABSENT, never ERR: we asked, and the answer is that it is gone.
            reply(seq, "ABSENT", "no live component with id " .. id_str
                  .. " (issued earlier, so it was probably destroyed or undocked since)")
            return
        end
    end
    local names = {...}
    if #names == 0 then names = DEFAULT_FIELDS end
    reply(seq, "OK", table.concat(read_fields(id, names), "\t"))
end

-- --------------------------------------------------------------------------- --
-- OBJECT FLAGS, and vanilla's own validity test
-- --------------------------------------------------------------------------- --
--
--: ★ WHY THIS EXISTS. MEASURED in game 2026-08-30, a per-object census of every argon
--: object in Argon Prime: of 163 ships, **11** have isknown=true. Of 13 stations, ONE.
--: `"Unknown Ship"` is exactly `isknown=false` (152 = 152, 12 = 12). So ~93% of every
--: reply this channel has ever sent was unlabelled, and nothing said so.
--:
--: The channel is NOT knowledge-limited -- only the LABELS are. Ids, positions, class,
--: owner and every flag come back for all 176. For research that is ground truth, so
--: we do NOT drop them. We report the flags, and we report vanilla's verdict as one
--: more column, letting the reader choose.
--:
--: `is_valid_object` is menu_map.lua:7548-7564 ported VERBATIM -- the clause order, the
--: classid-vs-realclassid split, all of it -- plus the separate mass-traffic drop at
--: menu_map.lua:7419. It is COPIED, not composed: an earlier version of this design
--: guessed a two-field class test and missed five predicates.
local FLAG_FIELDS = {
    "name", "owner", "classid", "realclassid", "sector", "sectorid",
    "isknown", "isradarvisible", "isdeployable", "isorphaned",
    "isattachedaslimpet", "iswreck", "isunit", "ismasstraffic", "isenemy", "isdocked",
}

--: ONE GetComponentData call for all of them. `read_fields` costs a call per field,
--: which is fine for a single `component` query and far too slow per row here.
--: select('#') rather than {f(...)}: a nil in the middle would give the table a hole
--: and silently shorten it, mapping every later value to the WRONG NAME.
--: ⚠ GetComponentData does NOT accept a raw UniverseID cdata, and it does not RAISE on
--: one -- it returns values that are simply not about any object. MEASURED in game
--: 2026-08-30: the --wide path handed it `buf[i]` straight from the ffi array and got
--: `unclassified=1539` out of 1539, i.e. every object unreadable, with `unreadable=0`
--: because nothing failed. The old enumerator always converted first
--: (`ConvertStringTo64Bit(tostring(buf[i]))`) and that is why it worked; dropping the
--: conversion during the rewrite is what broke it.
--:
--: Container objects are LuaIDs (MEASURED: `type(o)` is `userdata`) and vanilla passes
--: those to GetComponentData directly, so they go through untouched. Only the
--: canonical ULL rendering marks an id that still needs converting.
local function readable_id(obj)
    local s = tostring(obj)
    if s:match("^%d+ULL$") then return ConvertStringTo64Bit(s) end
    return obj
end

local function read_flags(obj)
    local id = readable_id(obj)
    local out = {}
    local function collect(...)
        for i = 1, select("#", ...) do out[FLAG_FIELDS[i]] = (select(i, ...)) end
    end
    local ok = pcall(function() collect(GetComponentData(id, table_unpack(FLAG_FIELDS))) end)
    if not ok then return nil end
    return out
end

--: TRI-STATE: true, false, or nil for COULD NOT TELL. nil must never collapse into
--: false -- an object we failed to classify is not an object we classified as "not a
--: ship", and rendering them alike is the silent narrowing this channel exists to
--: refuse.
--:
--: ⚠ FOUND IN GAME 2026-08-30, and it would have shipped: `Helper.isComponentClass`
--: is `componentClassLookup[class1 * 1000 + classIDs[class2]]` (helper.lua:782), so a
--: nil classid is ARITHMETIC ON NIL and raises. Some objects in the galaxy-wide
--: faction list have no class data, so `compare` and `objects --wide` both died on
--: the first one. The container path never hit it, which is exactly why the old path
--: had to stay reachable and be exercised.
local function class_is(v, name)
    if v == nil then return nil end
    if type(Helper) ~= "table" or type(Helper.isComponentClass) ~= "function" then
        return nil
    end
    local ok, r = pcall(Helper.isComponentClass, v, name)
    if not ok then return nil end
    return r and true or false
end

local function is_ship(f)    return class_is(f.classid, "ship") end
local function is_station(f) return class_is(f.realclassid, "station") end

-- Read-only fitted-equipment capture. Native analogues: ship configuration's
-- prepareComponentUpgradeSlots (1-based slots), GetUpgradeGroups2 and
-- GetUpgradeGroupInfo2 (0-based returned buffer). This is ONE callback, not an
-- engine-wide atomic snapshot, and has not yet been qualified in a live game.
do
    local SLOT_CAP, GROUP_CAP, CALL_CAP = 1024, 256, 8192
    local ROW_BYTES, PAGE_BYTES, CACHE_BYTES, TTL = 240000, 30000, 262144, 60
    local TYPES = {"engine", "shield", "turret", "weapon"}
    local GROUP_TYPES = {"engine", "shield", "turret"}
    local held, serial = nil, 0
    -- Separate declarations: missing equipment support must not disable other verbs.
    -- Reuse the native types when a UI menu declared them first. A private anonymous
    -- equal-layout struct is NOT pointer-compatible with the retained native function
    -- signature (measured with a real LuaJIT callback, without calling the engine).
    local declared = false
    if ffi then declared = pcall(function()
        local function native_type(name, definition)
            if not pcall(function() return ffi.typeof(name) end) then ffi.cdef(definition) end
        end
        native_type("UpgradeGroup", [[
            typedef struct { const char* path; const char* group; } UpgradeGroup;
        ]])
        native_type("UpgradeGroup2", [[
            typedef struct { UniverseID contextid; const char* path; const char* group; } UpgradeGroup2;
        ]])
        native_type("UpgradeGroupInfo", [[
            typedef struct {
                UniverseID currentcomponent; const char* currentmacro; const char* slotsize;
                uint32_t count; uint32_t operational; uint32_t total;
            } UpgradeGroupInfo;
        ]])
        ffi.cdef([[
        size_t GetNumUpgradeSlots(UniverseID, const char*, const char*);
        UpgradeGroup GetUpgradeSlotGroup(UniverseID, const char*, const char*, size_t);
        UniverseID GetUpgradeSlotCurrentComponent(UniverseID, const char*, size_t);
        const char* GetUpgradeSlotCurrentMacro(UniverseID, UniverseID, const char*, size_t);
        uint32_t GetNumUpgradeGroups(UniverseID, const char*);
        uint32_t GetUpgradeGroups2(UpgradeGroup2*, uint32_t, UniverseID, const char*);
        UpgradeGroupInfo GetUpgradeGroupInfo2(UniverseID, const char*, UniverseID,
                                             const char*, const char*, const char*);
        ]])
    end) end
    local function integer(v)
        local n = tonumber(v)
        if not n or n ~= n or n < 0 or n > 9007199254740991 or n ~= math.floor(n) then return nil end
        return n
    end
    local function clock()
        local ok, n = pcall(getElapsedTime)
        if not ok or type(n) ~= "number" or n < 0 or n ~= n or math.abs(n) == math.huge then return nil end
        return n
    end
    local function escaped(v)
        -- Percent-encode delimiters, percent itself, and terminal control bytes.
        return (tostring(v):gsub("[%%%c|]", function(c) return string.format("%%%02X", c:byte()) end))
    end
    local function row(kind, fields)
        local out = {kind}
        for i = 1, #fields, 2 do out[#out + 1] = fields[i] .. "=" .. escaped(fields[i + 1]) end
        return table.concat(out, "\t")
    end
    local function send_page(seq, token, page)
        local now = clock()
        if not held or held.token ~= token or held.issued ~= issued_ids then
            reply(seq, "ERR", "equipment capture absent, reloaded or superseded; start a fresh capture") return
        end
        if not now or now < held.finish or now - held.finish > TTL then
            held = nil
            reply(seq, "ERR", "equipment capture expired or clock reset; start a fresh capture") return
        end
        if not page or page < 1 or page > #held.pages then
            reply(seq, "ERR", "equipment page is outside this capture") return
        end
        -- Issue only verified fitted components on the page actually returned.
        -- Incomplete captures and group summaries never grant identity authority.
        for _, component in ipairs(held.identities[page] or {}) do
            issued_ids[component] = true
        end
        reply(seq, "OK", held.pages[page])
    end
    verbs["equipment-page"] = function(seq, token, page, extra)
        if extra or not page or not page:match("^[1-9]%d*$") or #page > 3 then
            reply(seq, "ERR", "equipment-page requires capture token and positive page number") return
        end
        send_page(seq, token, tonumber(page))
    end
    verbs.equipment = function(seq, id_str, nonce, extra)
        if extra or not nonce or #nonce ~= 32 or not nonce:match("^[0-9a-f]+$") then
            reply(seq, "ERR", "equipment requires issued ship id and a fresh 32-hex UUID nonce") return
        end
        if not id_str or not id_str:match("^%d+ULL$") or not issued_ids[id_str] then
            reply(seq, "ERR", "equipment id was not issued by this UI session; obtain a fresh ship id") return
        end
        if held and held.nonce == nonce then
            reply(seq, "ERR", "equipment nonce was already used; generate a fresh UUID") return
        end
        -- Any accepted new request invalidates the previous capture, even if it fails.
        held = nil
        local issued_at_start = issued_ids
        if not C or not declared then reply(seq, "ERR", "equipment FFI declarations unavailable") return end
        local start = clock()
        if not start then reply(seq, "ERR", "equipment UI elapsed clock unavailable") return end
        local calls, errors, limited = 0, 0, false
        local function call(fn)
            if calls >= CALL_CAP then limited = true; errors = errors + 1; return nil end
            calls = calls + 1
            local ok, val = pcall(fn)
            if not ok or val == nil then errors = errors + 1; return nil end
            return val
        end
        local function textval(v, cstring)
            if v == nil then errors = errors + 1; return nil end
            local ok, s = pcall(function() return cstring and ffi.string(v) or v end)
            if not ok or type(s) ~= "string" or s == "?" or #s > 256 then errors = errors + 1; return nil end
            return s
        end
        local function handle(v)
            if v == nil then errors = errors + 1; return nil end
            local ok, s = pcall(wire, v)
            if not ok or not s:match("^%d+ULL$") then errors = errors + 1; return nil end
            return s
        end
        local function count(fn)
            local n = call(fn)
            if n == nil then return nil end
            local i = integer(n)
            if not i then errors = errors + 1 end
            return i
        end
        local id = call(function() return ConvertStringTo64Bit(id_str) end)
        if not id then reply(seq, "ERR", "equipment ship handle conversion failed") return end
        local valid = call(function() return IsValidComponent(id) end)
        if valid == false then reply(seq, "ABSENT", "equipment ship no longer exists") return end
        if valid ~= true then reply(seq, "ERR", "equipment ship validity unreadable") return end
        local cls = call(function() return GetComponentData(id, "classid") end)
        local ship = call(function() return class_is(cls, "ship") end)
        -- A definite "not a ship" is an answer about the id; an unreadable class (no
        -- Helper, no classid, the lookup raising) is a non-answer. Different texts:
        -- the host treats only the first as a finding.
        if ship == false then reply(seq, "ERR", "equipment target is not a ship") return end
        if ship ~= true then reply(seq, "ERR", "equipment ship class unreadable") return end

        local rows, used, visits, kept, unknown = {}, 0, 0, 0, 0
        local totals, captured, slot_identities = {}, {}, {}
        local enum_complete = true
        local function add(s)
            if used + #s + 1 > ROW_BYTES then limited = true; return false end
            rows[#rows + 1] = s
            used = used + #s + 1
            return true
        end
        for _, typ in ipairs(TYPES) do
            local n = count(function() return C.GetNumUpgradeSlots(id, "", typ) end)
            totals[typ], captured[typ] = n or "?", 0
            if n == nil then enum_complete = false end
            for slot = 1, math.min(n or 0, SLOT_CAP - visits) do
                if limited then break end
                visits = visits + 1
                local before = errors
                local g = call(function() return C.GetUpgradeSlotGroup(id, "", typ, slot) end)
                local path = g and textval(g.path, true) or nil
                local group = g and textval(g.group, true) or nil
                local comp = handle(call(function() return C.GetUpgradeSlotCurrentComponent(id, typ, slot) end))
                local direct = typ == "engine" or (path == ".." and group == "")
                local slotmacro
                if direct then
                    slotmacro = textval(call(function() return C.GetUpgradeSlotCurrentMacro(id, 0, typ, slot) end), true)
                end
                local macro, state = nil, "unknown"
                if comp and comp ~= "0ULL" then
                    local cid = call(function() return readable_id(comp) end)
                    local live = cid and call(function() return IsValidComponent(cid) end)
                    if live == true then
                        macro = textval(call(function() return GetComponentData(cid, "macro") end), false)
                        if macro and macro ~= "" then state = "installed" end
                    else errors = errors + 1 end
                    if direct and slotmacro and macro and slotmacro ~= macro then
                        state = "unknown"; errors = errors + 1
                    end
                elseif comp == "0ULL" and direct and slotmacro == "" then
                    state = "empty"
                end
                if errors > before then state = "unknown" end
                local s = row("SLOT", {"type",typ,"slot",slot,"path",path or "?","group",group or "?",
                    "component",comp or "?","macro",macro or "?","slot_macro",slotmacro or "?",
                    "state",state,"read",errors == before and "ok" or "failed"})
                if not add(s) then break end
                if state == "installed" then slot_identities[s] = comp end
                kept = kept + 1; captured[typ] = captured[typ] + 1
                if state == "unknown" then unknown = unknown + 1 end
            end
            if n == nil or captured[typ] ~= n then enum_complete = false end
        end

        -- Group summaries are NOT fitted slots and are never added to slot totals.
        local ng = count(function() return C.GetNumUpgradeGroups(id, "") end)
        local returned, group_kept, group_summaries = nil, 0, 0
        if ng == nil then enum_complete = false end
        if ng and ng > GROUP_CAP then limited = true; enum_complete = false end
        local capacity = math.min(ng or 0, GROUP_CAP)
        if capacity > 0 and calls < CALL_CAP then
            local buf = ffi.new("UpgradeGroup2[?]", capacity)
            returned = count(function() return C.GetUpgradeGroups2(buf, capacity, id, "") end)
            if not returned or returned > capacity then
                errors = errors + 1; enum_complete = false
                returned = nil  -- never index an unproven buffer boundary
            else
                if returned ~= ng then enum_complete = false end
                for i = 0, returned - 1 do
                    local g, before = buf[i], errors
                    local context = handle(g.contextid)
                    local path, group = textval(g.path, true), textval(g.group, true)
                    if not add(row("GROUP", {"index",i,"context",context or "?","path",path or "?",
                        "group",group or "?","read",errors == before and "ok" or "failed"})) then break end
                    group_kept = group_kept + 1
                    -- Root/empty descriptors are enumerated but not group summaries,
                    -- matching the native menu. Failed identities remain explicit.
                    if context and path and group and (path ~= ".." or group ~= "") then
                        for _, typ in ipairs(GROUP_TYPES) do
                            local old = errors
                            local info = call(function()
                                return C.GetUpgradeGroupInfo2(id, "", g.contextid, path, group, typ)
                            end)
                            local comp, macro, size, cnt, operational, total
                            if info then
                                comp = handle(info.currentcomponent)
                                macro, size = textval(info.currentmacro, true), textval(info.slotsize, true)
                                cnt, operational, total = integer(info.count), integer(info.operational), integer(info.total)
                                if not cnt or not operational or not total then errors = errors + 1 end
                            end
                            if not add(row("SUMMARY", {"index",i,"type",typ,"context",context,
                                "path",path,"group",group,"component",comp or "?","macro",macro or "?",
                                "size",size or "?","count",cnt or "?","operational",operational or "?",
                                "total",total or "?","read",errors == old and "ok" or "failed"})) then break end
                            group_summaries = group_summaries + 1
                        end
                    end
                    if used >= ROW_BYTES or calls >= CALL_CAP then limited = true; break end
                end
            end
        elseif ng == 0 then returned = 0 end
        if group_kept ~= (ng or -1) then enum_complete = false end
        local still_valid = call(function() return IsValidComponent(id) end)
        if still_valid ~= true then errors = errors + 1 end
        local finish = clock()
        if not finish or finish < start then reply(seq, "ERR", "equipment capture clock reset") return end
        serial = serial + 1
        local token = nonce .. "-" .. serial
        local bodies, current, bytes = {}, {}, 0
        local identities, page_identities = {}, {}
        for _, s in ipairs(rows) do
            if bytes + #s + 1 > PAGE_BYTES then
                bodies[#bodies + 1] = table.concat(current, "\n"); current, bytes = {}, 0
                identities[#bodies] = page_identities; page_identities = {}
            end
            current[#current + 1] = s; bytes = bytes + #s + 1
            if slot_identities[s] then page_identities[#page_identities + 1] = slot_identities[s] end
        end
        bodies[#bodies + 1] = table.concat(current, "\n")
        identities[#bodies] = page_identities
        local pages, retained = {}, 0
        local fitting_complete = enum_complete and not limited and errors == 0 and unknown == 0
        for page, body in ipairs(bodies) do
            local head = row("EQUIPMENT", {"version",1,"token",token,"ship",id_str,"build",BUILD,
                "loaded_at",LOADED_AT,"start",start,"finish",finish,"clock","ui_elapsed",
                "atomic",false,"page",page,"pages",#bodies,
                "enumeration_complete",enum_complete and not limited,"reads_complete",errors == 0,
                "equipment_identities_complete",fitting_complete,"errors",errors,"limited",limited,
                "slots",kept,"unknown_slots",unknown,"groups_reported",ng or "?",
                "groups_returned",returned or "?","groups",group_kept,"summaries",group_summaries,
                "engine_reported",totals.engine,"engine_captured",captured.engine,
                "shield_reported",totals.shield,"shield_captured",captured.shield,
                "turret_reported",totals.turret,"turret_captured",captured.turret,
                "weapon_reported",totals.weapon,"weapon_captured",captured.weapon,
                "calls",calls,"slot_cap",SLOT_CAP,"group_cap",GROUP_CAP,"call_cap",CALL_CAP,
                "row_bytes_cap",ROW_BYTES,"page_bytes_cap",MAX_PAYLOAD,"cache_bytes_cap",CACHE_BYTES,"ttl",TTL})
            local payload = head .. "\n" .. body
            if #payload > MAX_PAYLOAD then reply(seq, "ERR", "equipment page exceeds bound") return end
            pages[page] = payload; retained = retained + #payload
        end
        if retained > CACHE_BYTES then reply(seq, "ERR", "equipment cache exceeds bound") return end
        if issued_ids ~= issued_at_start or issued_ids[id_str] ~= true then
            reply(seq, "ERR", "equipment identity epoch changed during capture") return
        end
        held = {token=token, nonce=nonce, finish=finish, issued=issued_at_start, pages=pages,
            identities=fitting_complete and identities or {}}
        send_page(seq, token, 1)
    end
end


--: Returns true/false, or nil if it could not be decided (Helper missing, or a class
--: lookup raised). nil is NOT false -- "could not tell" and "no" must never render
--: alike, so the row shows `?` and the header counts it separately.
local function is_valid_object(f)
    if type(Helper) ~= "table" or type(Helper.isComponentClass) ~= "function" then
        return nil
    end
    if f.classid == nil or f.realclassid == nil then return nil end
    local ok, verdict = pcall(function()
        local isship = is_ship(f) == true
        if not isship
           and not (is_station(f) and (not f.iswreck))
           and not f.isdeployable
           and not Helper.isComponentClass(f.classid, "lockbox")
           and not Helper.isComponentClass(f.classid, "collectablewares")
           and not (Helper.isComponentClass(f.classid, "buildstorage") and f.isorphaned) then
            return false
        elseif (isship or Helper.isComponentClass(f.classid, "controllable")) and f.isunit then
            return false
        elseif (not f.isknown) or (not f.isradarvisible) then
            return false
        elseif isship and f.isattachedaslimpet then
            return false
        end
        -- menu_map.lua:7419, a SEPARATE drop from isObjectValid and easy to miss:
        -- non-enemy mass traffic is removed from the rendered list.
        if f.ismasstraffic and (not f.isenemy) then return false end
        return true
    end)
    if not ok then return nil end
    return verdict
end

--: Compact, fixed-width flag column, so a row stays cheap against the payload cap and
--: a human can scan it. A letter means true, `-` means false.
local FLAG_LETTERS = {
    { "isknown", "k" }, { "isdocked", "d" }, { "ismasstraffic", "m" },
    { "isunit", "u" }, { "iswreck", "w" }, { "isenemy", "e" },
}
local function flag_string(f, valid)
    local s = {}
    for i = 1, #FLAG_LETTERS do
        local key, letter = FLAG_LETTERS[i][1], FLAG_LETTERS[i][2]
        s[#s + 1] = f[key] and letter or "-"
    end
    s[#s + 1] = (valid == nil) and "?" or (valid and "v" or "-")
    return table.concat(s)
end

-- --------------------------------------------------------------------------- --
-- ENUMERATION -- one primitive, one implementation
-- --------------------------------------------------------------------------- --
--
--: `objects` is the core; `stations` and `ships` are wrappers over it, so there is ONE
--: implementation of the count-then-fill contract rather than two that drift.
--:
--: TWO PATHS, and the header always names which ran:
--:   container (default) -- GetContainedObjectsByOwner(faction [, sector]). The engine
--:                          does the sector filtering. MEASURED: argon 2017 galaxy-wide
--:                          vs 279 in one sector.
--:   wide (--wide)       -- the OLD ffi walk: GetAllFactionShips/Stations over the whole
--:                          galaxy, filtered by sectorid in lua. Kept as an EXPLICIT
--:                          opt-in, never an automatic fallback: an empty sector and a
--:                          failed call must never render alike. It is also the
--:                          reference that `compare` diffs against.

--: The faction list. Verbatim idiom from customgame.lua:4921-4923 (second copy at
--: menu_mapeditor.lua:338). Keep a lua reference to the buffer for as long as it is
--: read -- helper.lua:8956 documents that it can otherwise be collected too early.
--: `hidden` false excludes `civilian` (mass traffic) and `criminal`, which carry
--: tags="hidden" in libraries/factions.xml.
local function faction_list(hidden)
    local n = tonumber(C.GetNumAllFactions(hidden)) or 0
    if n == 0 then return {} end
    local buf = ffi.new("const char*[?]", n)
    local got = tonumber(C.GetAllFactions(buf, n, hidden)) or 0
    local out = {}
    for i = 0, got - 1 do out[#out + 1] = ffi.string(buf[i]) end
    return out
end

--: Gather raw object handles. Returns list, meta -- and meta records what was ASKED as
--: well as what came back, because a faction whose call FAILED is not the same as a
--: faction that owns nothing, and one number cannot say both.
local function gather(opts)
    local ids, seen = {}, {}
    local meta = { factions = 0, failed = {}, dupes = 0 }

    local function take(o)
        -- Dedupe on the RAW handle string: unique per object and free, where `wire`
        -- would cost an engine call per object just to discover there are no dupes.
        local raw = tostring(o)
        if seen[raw] then meta.dupes = meta.dupes + 1 return end
        seen[raw] = true
        ids[#ids + 1] = o
    end

    if opts.wide then
        -- The old path. It needs a named faction: there is no all-factions form of it,
        -- which is part of why the container primitive is the better core.
        local kinds = {}
        if opts.class == "station" or opts.class == "all" then kinds[#kinds + 1] = "stations" end
        if opts.class == "ship"    or opts.class == "all" then kinds[#kinds + 1] = "ships" end
        meta.factions = 1
        meta.total = 0
        for _, kind in ipairs(kinds) do
            local numfn = (kind == "stations") and C.GetNumAllFactionStations or C.GetNumAllFactionShips
            local getfn = (kind == "stations") and C.GetAllFactionStations   or C.GetAllFactionShips
            local total = tonumber(numfn(opts.faction)) or 0
            meta.total = meta.total + total
            if total > 0 then
                local buf = ffi.new("UniverseID[?]", total)
                local got = tonumber(getfn(buf, total, opts.faction)) or 0
                for i = 0, got - 1 do take(buf[i]) end
            end
        end
    else
        local container = nil
        if opts.sector ~= nil then container = ConvertStringToLuaID(tostring(opts.sector)) end
        local factions = opts.faction and { opts.faction } or faction_list(opts.hidden and true or false)
        meta.factions = #factions
        for _, fac in ipairs(factions) do
            -- Per-faction pcall: one faction the engine dislikes must not cost us the
            -- other 26. Failures are NAMED, never absorbed into a smaller number.
            local ok, lst = pcall(GetContainedObjectsByOwner, fac, container)
            if ok and type(lst) == "table" then
                for _, o in ipairs(lst) do take(o) end
            else
                meta.failed[#meta.failed + 1] = fac
            end
        end
        -- The galaxy-wide denominator, for parity with the old header. Only computed
        -- for a single faction: asking it of 27 would mean 54 more galaxy-wide walks,
        -- which is the exact cost this path exists to remove.
        if opts.faction then
            local a = tonumber(C.GetNumAllFactionShips(opts.faction)) or 0
            local b = tonumber(C.GetNumAllFactionStations(opts.faction)) or 0
            -- ⚠ total must count the SAME POPULATION as `enumerated`, or it is not a
            -- denominator, it is a different number standing next to one. MEASURED in
            -- game: class=all gave matched=1909 against total=1813, i.e. MORE matches
            -- than the "total" -- because the container returns every class (drones,
            -- buildstorage, deployables) while GetNumAllFaction* counts only ships and
            -- stations. For a mixed-class query there is no cheap true denominator, so
            -- it says so and reports the ship+station figure under its own name.
            if opts.class == "ship" then meta.total = a
            elseif opts.class == "station" then meta.total = b
            else meta.ships_stations = a + b end
        end
    end
    return ids, meta
end

--: ⚠ THE HEADER IS SPACE-DELIMITED `key=value`, AND A SECTOR TOKEN CONTAINS A SPACE.
--: The engine writes sectorid as `ID: 7479`, so emitting it raw produced
--: `sector=ID: 7479` -- which any consumer splitting on whitespace reads as
--: `sector=ID:` plus a stray `7479`. Caught by a test helper that parses the header
--: the same way a caller would. Whitespace is stripped for the HEADER only; the token
--: is still matched and accepted in its original form everywhere else.
local function header_safe(v)
    return (tostring(v):gsub("%s+", ""))
end

--: Render. Shared by every verb, so the row schema and the budget rule exist once.
local function render(ids, meta, opts)
    local rows, used, shown, matched, nvalid, nundecided = {}, 0, 0, 0, 0, 0
    local capped, nunreadable, nunclassified = false, 0, 0
    local nunconvertible = 0

    local enum_capped = false
    for i, obj in ipairs(ids) do
        -- ABSOLUTE BOUND ON THE WORK, distinct from the bound on the REPLY. Even
        -- after the cost fix below, ~4.5 engine calls run per enumerated object, in
        -- ONE frame -- and an all-factions galaxy query is tens of thousands of
        -- objects. This is set far above any real query so it never fires in normal
        -- use, and when it does it SAYS SO in the header instead of quietly
        -- returning less. `matched` then describes what was WALKED, not what exists,
        -- which is exactly why it has to be declared.
        if i > MAX_ENUMERATE then enum_capped = true break end
        --: PER-OBJECT CONTAINMENT. `wire` RAISES on an id at or above 2^53, and
        --: that raise used to be caught only by the verb's single outer pcall --
        --: so ONE unconvertible object discarded the entire enumeration and every
        --: denominator with it. MEASURED with 5 objects, one of them at 2^53: the
        --: verb returned `ERR enumeration raised: ... cannot be represented
        --: exactly`, and the four good rows, `shown`, `matched` and `enumerated`
        --: were all lost. The control run reported shown=5 matched=5.
        --:
        --: The 2^53 enforcement is right; the CONTAINMENT was at the wrong
        --: granularity. `read_flags` two lines down already shows the pattern --
        --: per object, counted, and announced in the header -- and that is what
        --: this now does. It matters beyond the 2^53 route, which is remote at
        --: today's ~10^7 ids: the same outer pcall swallows ANY per-object raise,
        --: and `Helper.isComponentClass` raising on a nil classid is the failure
        --: that already killed `compare` and `objects --wide` once.
        local ok_obj, f = pcall(read_flags, obj)
        if not ok_obj then
            nunconvertible = nunconvertible + 1
            f = nil
        end
        if f == nil then
            nunreadable = nunreadable + 1
        else
            local keep
            if opts.class == "all" then
                keep = true
            else
                -- ⚠ NOT `cond and is_ship(f) or is_station(f)`. When is_ship returns
                -- FALSE the and/or form falls through to the `or` branch, so asking
                -- for ships also matched every station. Caught by the class test; the
                -- same trap is documented in the fake's GetComponentData.
                local verdict
                if opts.class == "ship" then verdict = is_ship(f)
                else verdict = is_station(f) end
                if verdict == nil then nunclassified = nunclassified + 1 end
                keep = (verdict == true)
            end
            -- SECTOR filter, only on the wide path: the container path already filtered
            -- engine-side, and re-testing it here would silently hide any disagreement
            -- between the two -- which is precisely what `compare` exists to find.
            if keep and opts.wide and opts.sector ~= nil then
                keep = (tostring(f.sectorid) == tostring(opts.sector))
            end
            if keep then
                matched = matched + 1
                local valid = is_valid_object(f)
                if valid == true then nvalid = nvalid + 1
                elseif valid == nil then nundecided = nundecided + 1 end

                -- COST BOUND. The four engine calls below (wire,
                -- ConvertStringTo64Bit, position, class) ran for EVERY matched object
                -- and the result was then discarded by the byte test at the bottom.
                -- MEASURED with the repo's own lupa harness at N=5000: 40,492 engine
                -- calls for a reply of 489 rows -- 8.10 per object, in ONE frame,
                -- because pipes.lua drains the FIFO inside a single
                -- Time.Register_NewFrame_Callback. After: 22,452 (4.49), a 45% cut,
                -- with shown/matched AND the reply checksum unchanged (489/5000,
                -- 2474899377 on both sides).
                --
                -- THIS IS A REAL SEMANTIC CHANGE, stated rather than buried: the old
                -- loop kept trying every later row, so a SHORTER one could still
                -- squeeze in after a longer one was rejected -- best fit. This is
                -- first fit. On uniform rows the reply is byte-identical; on ragged
                -- ones it may hold one or two fewer. `omitted` stays exact either way
                -- (matched - shown), and matched/nvalid/nundecided are still counted
                -- for EVERY object, so the honest denominator the comment below
                -- defends is untouched -- which was the whole reason it refused to
                -- `break` in the first place.
                --
                -- A lossless variant was tried first: stop building once not even the
                -- shortest POSSIBLE row could fit. MEASURED: it saved NOTHING. After
                -- the cap, `used` sits ~60 bytes under the budget, which is still room
                -- for a minimum-length row, so the guard never fires. Lossless and
                -- inert is worse than honest and effective.
                if capped then
                    capped = true
                else
                    --: THE RAISE LIVES HERE. `wire` enforces the 2^53 rule by
                    --: raising, and this is the first call that reaches it for a
                    --: matched object -- so containment has to wrap THIS, not only
                    --: read_flags above. Counted into the same disclosure.
                    local ok_id, id_str = pcall(wire, obj)
                    if not ok_id then
                        --: NO goto: this must run under the engine's Lua, and 5.1 has
                        --: none. A conditional costs one indent and cannot be wrong.
                        nunconvertible = nunconvertible + 1
                        else
                        local id64 = ConvertStringTo64Bit(id_str)
                        local px, py, pz = "?", "?", "?"
                        local pok, pos = pcall(C.GetObjectPositionInSector, id64)
                        if pok and pos ~= nil then
                            px = string.format("%.0f", pos.x)
                            py = string.format("%.0f", pos.y)
                            pz = string.format("%.0f", pos.z)
                        end
                        local cls = "?"
                        local cok, craw = pcall(C.GetComponentClass, id64)
                        if cok and craw ~= nil then cls = ffi.string(craw) end

                        local row = id_str .. "|" .. cls .. "|" .. (tostring(f.name):gsub("|", "/"))
                                    .. "|" .. tostring(f.owner) .. "|" .. tostring(f.sector)
                                    .. "|" .. px .. "," .. py .. "," .. pz
                                    .. "|" .. flag_string(f, valid)
                        -- BOUND BEFORE SENDING: an over-long reply tears the pipe down, so
                        -- this is enforced here, never detected afterwards. NB we do NOT
                        -- break -- stopping the loop would stop counting matches too, and
                        -- then `matched` would just be `shown` again, destroying the
                        -- honest denominator.
                        if used + #row + 1 > ROW_BUDGET then
                            capped = true
                        else
                            issue(obj)
                            rows[#rows + 1] = row
                            used = used + #row + 1
                            shown = shown + 1
                        end
                    end
                end
            end
        end
    end

    local header = "shown=" .. shown .. " matched=" .. matched
                   .. (enum_capped and (" ENUM_CAPPED=yes limit=" .. MAX_ENUMERATE
                       .. " walked=" .. MAX_ENUMERATE .. "/" .. #ids) or "")
                   .. " enumerated=" .. #ids
                   .. " total=" .. (meta.total and tostring(meta.total)
                        or (meta.ships_stations
                            and ("n/a(mixed-classes) ships_stations=" .. meta.ships_stations)
                            or "n/a(all-factions)"))
                   .. " sector=" .. (opts.sector and header_safe(opts.sector) or "galaxy")
                   .. " class=" .. opts.class
                   .. " factions=" .. meta.factions
                   .. " hidden=" .. ((not opts.wide) and (opts.hidden and "yes" or "no") or "n/a")
                   .. " path=" .. (opts.wide and "wide" or "container")
                   .. " valid=" .. nvalid
    -- Every narrowing announces itself. These lines appear only when non-zero, but a
    -- zero is genuinely "none of this happened" -- unlike an omitted line, which would
    -- be a silent loss, the one thing this channel must never do.
    if nundecided > 0 then header = header .. " undecided=" .. nundecided end
    if nunreadable > 0 then header = header .. " unreadable=" .. nunreadable end
    if nunconvertible > 0 then header = header .. " unconvertible=" .. nunconvertible end
    if nunclassified > 0 then header = header .. " unclassified=" .. nunclassified end
    if meta.dupes > 0 then header = header .. " dupes_dropped=" .. meta.dupes end
    if #meta.failed > 0 then
        header = header .. " factions_failed=" .. #meta.failed
                 .. "(" .. table.concat(meta.failed, ",") .. ")"
    end
    header = header .. (capped
        and (" CAPPED=yes omitted=" .. (matched - shown)
             .. " (reply cap " .. MAX_PAYLOAD .. " bytes; narrow with a sector, a class"
             .. " or a faction)")
        or " CAPPED=no")
    table.insert(rows, 1, header)

    -- The reserve is an ASSUMPTION about header length; this is the check that makes it
    -- a knowable one rather than a hope.
    local size = #table.concat(rows, "\t")
    if size > MAX_PAYLOAD then
        DebugError("X4TOOLKIT_LIVE payload " .. size .. " EXCEEDS the " .. MAX_PAYLOAD
                   .. "-byte budget; HEADER_RESERVE (" .. HEADER_RESERVE
                   .. ") is too small for a " .. #header .. "-byte header")
    end
    return rows
end

--: Pull the flag tokens out of the positional arguments. They may appear anywhere, so
--: a caller need not remember an order that exists only inside our parser.
local function split_flags(...)
    local opts, pos = { hidden = false, wide = false, contents = false, deep = false }, {}
    for _, a in ipairs({ ... }) do
        if a == "--hidden" then opts.hidden = true
        elseif a == "--wide" then opts.wide = true
        elseif a == "--contents" then opts.contents = true
        elseif a == "--deep" then opts.deep = true
        elseif a ~= nil and a ~= "" then pos[#pos + 1] = a end
    end
    return opts, pos
end

local VALID_CLASSES = { ship = true, station = true, all = true }

--: ★ AN INVALID CONTAINER IS SILENTLY IGNORED BY THE ENGINE, AND YOU GET THE WHOLE
--: GALAXY BACK LABELLED AS YOUR SECTOR. MEASURED in game 2026-08-30, same faction,
--: seconds apart:
--:     objects "ID: 514068" -> 310 objects   (correctly scoped)
--:     objects "ID:"        -> 1961 objects  labelled sector=ID:
--:     objects "NOT_A_SECTOR" -> 1961        labelled sector=NOT_A_SECTOR
--: No error, no flag, 6x the data. This is the narrowing-without-announcement defect
--: inverted: a step that FAILED to narrow, reporting as though it had. Sector tokens
--: change every launch, so a stale one is the normal case, not an exotic one.
--:
--: So the shape is checked HERE rather than trusted. `-`/`galaxy` are already resolved
--: to nil before this and mean the galaxy deliberately, which is a different thing
--: from a token that did not work.
local function bad_sector_token(tok)
    if tok == nil then return nil end
    if tostring(tok):match("^ID:%s*%d+$") then return nil end
    return "sector token '" .. tostring(tok) .. "' is not the shape the engine uses "
           .. "(ID: <digits>, as `player` reports it). An unrecognised container is "
           .. "SILENTLY IGNORED by the engine and would return every object the owner "
           .. "has anywhere, labelled as this sector -- MEASURED 310 vs 1961. Re-read "
           .. "the token from `player`; they change every launch. Use - for the galaxy."
end

local function run_enumeration(seq, opts)
    if not cdef_ok then
        reply(seq, "ERR", "enumeration needs the ffi cdef block, which did not load")
        return
    end
    local bad = bad_sector_token(opts.sector)
    if bad then reply(seq, "ERR", bad) return end
    if opts.wide and not opts.faction then
        reply(seq, "ERR", "--wide is the old per-faction walk and has no all-factions "
                          .. "form; name a faction")
        return
    end
    if not opts.wide and type(GetContainedObjectsByOwner) ~= "function" then
        reply(seq, "ERR", "GetContainedObjectsByOwner is absent; retry with --wide "
                          .. "(which needs a faction)")
        return
    end
    local ok, res = pcall(function()
        local ids, meta = gather(opts)
        return render(ids, meta, opts)
    end)
    if not ok then reply(seq, "ERR", "enumeration raised: " .. tostring(res)) return end
    reply(seq, "OK", table.concat(res, "\t"))
end

--: objects <sector> [class] [faction] [--hidden] [--wide]
--: A sector of - or galaxy means no container: every object the owner(s) hold.
--:
--: NOT A CENSUS, and the header says so. Ownerless objects (wrecks, lockboxes,
--: asteroids) belong to no faction and are invisible to ANY owner query; hidden
--: factions are opt-in; and name/sector are player-knowledge, so MEASURED ~93% come
--: back "Unknown" while ids, positions, class and flags stay exact.
verbs.objects = function(seq, ...)
    local opts, pos = split_flags(...)
    local sector, class, faction = pos[1], pos[2], pos[3]
    if sector == nil then
        reply(seq, "ERR", "objects needs a sector token, the one `player` reports as "
                          .. "sectorid, or - for the whole galaxy")
        return
    end
    if sector == "-" or sector == "galaxy" then sector = nil end
    class = class or "all"
    if not VALID_CLASSES[class] then
        reply(seq, "ERR", "class must be ship, station or all -- got " .. tostring(class))
        return
    end
    opts.sector, opts.class, opts.faction = sector, class, faction
    run_enumeration(seq, opts)
end

--: representative <faction>: the faction's representative NPC, ISSUED so a bound action may name it (the
--: licence menu takes the trader as its param[3]; without one the trader is the player's own computer).
--: READ vanilla: declared menu_encyclopedia.lua:101 (only cdef), called this way at menu_diplomacy.lua:1657.
--: Read-only; 0ULL -> ABSENT.
verbs.representative = function(seq, faction)
    if not cdef_ok then
        reply(seq, "ERR", "representative needs the ffi cdef block, which did not load")
        return
    end
    if type(faction) ~= "string" or #faction > 64 or not faction:match("^%a[%w_]*$") then
        reply(seq, "ERR", "representative needs a faction id, e.g. representative terrantrader")
        return
    end
    local ok, raw = pcall(C.GetFactionRepresentative, faction)
    if not ok then reply(seq, "ERR", "GetFactionRepresentative raised: " .. tostring(raw)) return end
    local wok, s = pcall(wire, raw)
    if not wok then reply(seq, "ERR", "could not convert the representative id") return end
    if s == "0ULL" then reply(seq, "ABSENT", "faction " .. faction .. " has no representative") return end
    issue(raw)
    local nok, name = pcall(GetComponentData, ConvertStringTo64Bit(s), "name")
    local shown = nok and (tostring(name):gsub("|", "/")) or "?"
    reply(seq, "OK", "representative=" .. s .. "|faction=" .. faction .. "|name=" .. shown)
end

verbs.stations = function(seq, faction, ...)
    local opts, pos = split_flags(...)
    if faction == nil or faction == "" then
        reply(seq, "ERR", "stations needs a faction id, e.g. stations argon")
        return
    end
    opts.sector, opts.class, opts.faction = pos[1], "station", faction
    run_enumeration(seq, opts)
end

verbs.ships = function(seq, faction, ...)
    local opts, pos = split_flags(...)
    if faction == nil or faction == "" then
        reply(seq, "ERR", "ships needs a faction id, e.g. ships argon")
        return
    end
    opts.sector, opts.class, opts.faction = pos[1], "ship", faction
    run_enumeration(seq, opts)
end

-- --------------------------------------------------------------------------- --
-- `compare <faction> <sector>` -- the ATOMIC old-vs-new proof
-- --------------------------------------------------------------------------- --
--
--: ★ WHY THIS IS ONE CALL AND NOT TWO QUERIES. MEASURED 2026-08-30: argon went from
--: 1931 to 2017 owned objects in about five minutes -- mass traffic spawns and
--: despawns continuously. So a diff between two SEPARATE queries shows hundreds of
--: differences that are pure drift, with no way to tell them from a real difference
--: between the two enumeration methods. The comparison is only meaningful if both
--: enumerations happen in the SAME lua call, in the same frame. It cost one wrong plan
--: to notice that, and the earlier "contradiction" that prompted it (23 in-sector
--: objects that could not fit in 13 galaxy-wide) turned out to be drift too.
--:
--: Both sides are restricted to ship|station, because the wide path CANNOT return
--: anything else -- comparing it against an unrestricted container list would report
--: every drone as a difference and prove nothing.
--:
--: `wire_same_object` renders ONE object present in BOTH sets through both conversion
--: paths. That is the measurement that settles whether the ffi and bare-global id
--: forms really differ; up to here it was inferred from two DIFFERENT objects.
local COMPARE_ROWS = 12

verbs.compare = function(seq, faction, sector)
    if not cdef_ok then
        reply(seq, "ERR", "compare needs the ffi cdef block, which did not load")
        return
    end
    if faction == nil or faction == "" or sector == nil or sector == "" then
        reply(seq, "ERR", "compare needs a faction and a sector token, e.g. "
                          .. "compare argon <the sectorid player reports>")
        return
    end
    if type(GetContainedObjectsByOwner) ~= "function" then
        reply(seq, "ERR", "GetContainedObjectsByOwner is absent; nothing to compare")
        return
    end
    -- The same guard as run_enumeration: an unrecognised container silently widens the
    -- container side to the whole galaxy, which here would manufacture a huge bogus
    -- new_only set and read as a method difference.
    local bad = bad_sector_token(sector)
    if bad then reply(seq, "ERR", bad) return end
    local ok, res, refused = pcall(function()
        local old_raw = gather({ wide = true,  class = "all", faction = faction, sector = sector })
        local new_raw = gather({ wide = false, class = "all", faction = faction, sector = sector })

        -- ABSOLUTE BOUND ON THE WORK, and it REFUSES rather than truncating.
        -- MEASURED offline at N=5,000: 30,008 engine calls, all inside the one
        -- frame pipes.lua drains the FIFO in. `render` has carried this bound
        -- since the round-6 finding; compare never called render, so the bound
        -- was never on this path.
        --
        -- ⚠ It must NOT cap the way `render` does. These two lists feed ONE
        -- set difference, so truncating either side drops objects out of
        -- `old_set` or `new_set` and every one of them then reads as
        -- `new_only`/`old_only` -- the exact method difference this verb exists
        -- to detect, manufactured out of a performance fix. censusprobe's
        -- `#factions == 0` guard is the same hazard written down, and it too
        -- refuses rather than reporting.
        --
        -- So: an ERR naming both numbers, which is `reply`'s own contract for an
        -- over-budget frame a few hundred lines up ("it must never be SENT: it
        -- becomes an ERR naming the size, which is a diagnosis the caller can act
        -- on"). Checked BEFORE the first read_flags, so a refusal costs the two
        -- enumeration calls and nothing else.
        if #old_raw + #new_raw > MAX_ENUMERATE then
            return nil, ("ENUM_REFUSED wide=" .. #old_raw .. " container=" .. #new_raw
                .. " limit=" .. MAX_ENUMERATE .. " -- comparing these would walk "
                .. (#old_raw + #new_raw) .. " objects at ~5 engine calls each inside "
                .. "ONE frame. Truncating is not available here: both sides feed one "
                .. "set difference, so a partial walk would report the missing "
                .. "objects as a method difference. Narrow it -- a smaller faction, "
                .. "or `objects <sector>` if you want a listing rather than a "
                .. "comparison.")
        end

        local old_set, new_set, flags = {}, {}, {}
        local luaid_type, old_type = "<none>", "<none>"
        local n_old_unreadable, n_new_unreadable = 0, 0

        for _, o in ipairs(old_raw) do
            if old_type == "<none>" then old_type = type(o) end
            local f = read_flags(o)
            if f == nil then n_old_unreadable = n_old_unreadable + 1
            elseif (is_ship(f) == true or is_station(f) == true)
                   and tostring(f.sectorid) == tostring(sector) then
                --: ISSUE, not wire. `wire` canonicalises; `issue` canonicalises AND
                --: records the id in the allowlist. `render` (:1388) and
                --: `censusprobe` both issue; `compare` alone did not -- so every id
                --: this verb PRINTS was absent from `issued_ids`, and feeding one
                --: straight back to `component` was refused with "most likely
                --: mistyped, or left over from before a UI reload". Both named
                --: causes are false: the tool printed the id one query earlier.
                --: compare's whole purpose is to name the objects the two
                --: enumerations disagree about -- its own comment calls those rows
                --: THE DIAGNOSIS -- so refusing to follow them, and blaming the
                --: operator for it, defeats the verb.
                local k = issue(o)
                old_set[k] = o
                flags[k] = f
            end
        end
        for _, o in ipairs(new_raw) do
            if luaid_type == "<none>" then luaid_type = type(o) end
            local f = read_flags(o)
            if f == nil then n_new_unreadable = n_new_unreadable + 1
            elseif is_ship(f) == true or is_station(f) == true then
                local k = issue(o)
                new_set[k] = o
                flags[k] = flags[k] or f
            end
        end

        local n_old, n_new, n_both = 0, 0, 0
        local only_old, only_new, common = {}, {}, nil
        for k in pairs(old_set) do
            n_old = n_old + 1
            if new_set[k] then
                n_both = n_both + 1
                common = common or k
            else
                only_old[#only_old + 1] = k
            end
        end
        for k in pairs(new_set) do
            n_new = n_new + 1
            if not old_set[k] then only_new[#only_new + 1] = k end
        end

        -- THE wire-form measurement, on ONE object that both paths returned.
        local wire_verdict
        if common == nil then
            wire_verdict = "<no object in both sets>"
        else
            local raw_old = tostring(old_set[common])
            local okc, conv = pcall(ConvertIDTo64Bit, new_set[common])
            local raw_new = okc and tostring(conv) or ("raised:" .. tostring(conv))
            wire_verdict = (raw_old == raw_new)
                and ("identical(" .. raw_old .. ")")
                or ("DIFFERS ffi=" .. raw_old .. " bare=" .. raw_new)
        end

        local out = {
            "old=" .. n_old .. " new=" .. n_new .. " both=" .. n_both
            .. " old_only=" .. #only_old .. " new_only=" .. #only_new
            .. " sector=" .. header_safe(sector) .. " faction=" .. header_safe(faction)
            .. " old_type=" .. old_type .. " new_type=" .. luaid_type
            .. " old_unreadable=" .. n_old_unreadable
            .. " new_unreadable=" .. n_new_unreadable,
            "wire_same_object=" .. wire_verdict,
        }
        -- The rows are the DIAGNOSIS, not decoration: if every old_only row carries `d`
        -- then the container form excludes docked ships, and that is the answer.
        local function dump(label, keys)
            for i = 1, math.min(#keys, COMPARE_ROWS) do
                local k = keys[i]
                local f = flags[k]
                out[#out + 1] = label .. "=" .. k .. "|" .. tostring(f and f.name)
                                .. "|" .. (f and flag_string(f, is_valid_object(f)) or "?")
            end
            if #keys > COMPARE_ROWS then
                out[#out + 1] = label .. "_truncated=" .. (#keys - COMPARE_ROWS)
            end
        end
        dump("old_only", only_old)
        dump("new_only", only_new)
        return out
    end)
    if not ok then reply(seq, "ERR", "compare raised: " .. tostring(res)) return end
    -- A REFUSAL is not a raise and must not be reported as one: one is this verb
    -- declining a query it cannot answer soundly, the other is a defect.
    if res == nil then reply(seq, "ERR", refused) return end
    reply(seq, "OK", table.concat(res, "\t"))
end
-- --------------------------------------------------------------------------- --
-- THE CAPABILITY HARVEST -- ask the ENGINE what it will answer; enumerate, do not guess
-- --------------------------------------------------------------------------- --
--
--: WHY THESE EXIST. Every capability this channel has gained was found by GUESSING a
--: name out of vanilla and trying it. That works, and it has a hard ceiling: it can only
--: ever confirm names somebody already thought of, so "the engine has no such thing" and
--: "nobody asked" produce an identical silence. MEASURED 2026-08-30: vanilla's
--: ui/addons passes 182 distinct field literals to GetComponentData across 886 call
--: sites; this mod reads 16. The gap is not knowledge, it is that nothing ever
--: ENUMERATED the surface.
--:
--: `globals` lists what the engine actually injected. `galaxyprobe` asks the container
--: and sector primitives what they DO rather than assuming from vanilla usage. Neither
--: builds anything on the answers: this is a harvest, and the fixture it produces is
--: what a later design gets to reason from.

--: NAMES AND TYPES ONLY -- THIS VERB NEVER CALLS WHAT IT FINDS. An unknown global may
--: mutate game state, and this verb is a READ: the mod's only writes are the two named
--: verbs in WRITE_VERBS, and they call nothing they discover. `type(v)` cannot run it;
--: `v()` can. There is deliberately no "call it and see" mode, and adding one would
--: change what this mod IS.
--:
--: Paged by BYTES rather than by row count, because the cap is a byte cap and an
--: over-long reply tears the pipe down instead of truncating.
--: ZERO-ARG, vanilla-attested, read-shaped, UI-widget names filtered out.
local RECON_ZERO = {
    "GetActiveGuidanceMissionComponent", "GetActiveMission", "GetAdapterOption",
    "GetAimAssistOption", "GetAllExtensionSettings", "GetAllStatIDs",
    "GetAutoPilotTarget", "GetAutorollOption", "GetAutosaveOption",
    "GetBonusContentData", "GetBoostToggleOption", "GetCaptureHQOption",
    "GetCollisionAvoidanceAssistOption", "GetControllerInfo", "GetCrashReportOption",
    "GetCurRealTime", "GetCurTime", "GetDeadzoneOption", "GetDistortionOption",
    "GetEffectDistanceOption", "GetExtensionList", "GetFOVOption",
    "GetFullscreenOption", "GetGamepadModeOption", "GetGammaOption",
    "GetGfxQualityOption", "GetGlobalSyncSetting", "GetGlowOption", "GetHoloMapColors",
    "GetInputActionMap", "GetInputProfiles", "GetInputRangeMap", "GetInputStateMap",
    "GetJoysticksOption", "GetLODOption", "GetLoadingInfo",
    "GetMappedJoysticks", "GetMenuParameters2", "GetNumErrors", "GetNumMissions",
    "GetPersonalizedCrashReportsOption", "GetPossibleAdapters",
    "GetPossibleResolutions", "GetRadarOption", "GetRegisteredModules",
    "GetResolutionOption", "GetRumbleOption", "GetSSAOOption",
    "GetShaderQualityOption", "GetShadowOption", "GetSoftShadowsOption",
    "GetSoundOption", "GetSteeringNoteOption", "GetStopShipInMenuOption",
    "GetSubtitleOption",
    "GetTradeShipList", "GetUISafeModeOption",
    "GetVentureOutcomes", "GetVersionString", "HasFlightControl", "IsCheatVersion",
    "IsDialogActive", "IsFirstPerson", "IsLuaDebugInputEnabled",
    "IsOnlineSavePossible", "IsSofttargetLocked", "IsSteamworksEnabled"
}

--: ONE-ARG taking a COMPONENT/OBJECT id. Probed with ids this session issued.
local RECON_OBJ = {
    "CanViewLiveData", "GetAllCommanders", "GetAllWeapons",
    "GetAmmoCountAfterTradeOrders", "GetBuildAnchor", "GetCollectableData",
    "GetCommander", "GetControlEntity", "GetNPCs",
    "GetPrioritizedPlatformNPCs", "GetProductionModules", "GetStorageData",
    "GetSubordinates", "GetUnitStorageData",
    "GetWorkForceRaceResources", "IsComponentOperational"
}

--: ONE-ARG taking a MACRO NAME -- NOT an object id.
--:
--: F88. `GetMacroUnitStorageCapacity` sat in RECON_OBJ until 2026-08-31 and was called
--: with an object id. It returned `OK number:0`: a benign-looking WRONG ANSWER with no
--: error raised, which is F86's shape exactly, and `0` is indistinguishable from "this
--: ship carries no units" -- which is why nothing caught it.
--:
--: Vanilla passes it a MACRO, two ways, and we copy the second verbatim rather than
--: compose one:
--:     menu_map.lua:10461  GetMacroUnitStorageCapacity(menu.macro)
--:     menu_map.lua:10468  GetMacroUnitStorageCapacity(GetComponentData(id, "macro"))
--:
--: The lesson generalises past this one name: a probe list keyed on ARGUMENT POSITION
--: is not keyed on anything. What decides the bucket is what VANILLA PASSES.
local RECON_MACRO = {
    "GetMacroUnitStorageCapacity",
    -- F88, FIFTH instance, found 2026-08-31 by re-deriving the whole list
    -- rather than trusting the one name that had been proven. Vanilla:
    --   menu_map.lua:30321  GetTransportUnitMacros(GetComponentData(
    --                         ship.shipid, "macro"))
    -- It sat in RECON_OBJ and was called with an object id. Live, it returned
    -- `OK|` -- EMPTY, not an error. Same silent shape as the original.
    "GetTransportUnitMacros"
}

--: ONE-ARG taking a FACTION id.
local RECON_FAC = {
    "GetOwnLicences", "IsFactionKnown"
}

--: ★★ RECON -- the ONLY verb in this mod that CALLS what it enumerates, and the rules
--: that make that safe are not negotiable.
--:
--: `globals` deliberately never calls: an unknown engine global may mutate state. This
--: verb calls, so every name it touches had to earn it three ways:
--:   1. READ-SHAPED by name (Get/Is/Has/Find/Are/Can). Set/Add/Remove/... never appear.
--:   2. ATTESTED IN VANILLA -- MEASURED over 81 vanilla ui lua files. 46 of the 308
--:      candidates are NOT called anywhere in vanilla and are therefore NOT probed.
--:      "It exists in _G" is not evidence that calling it is safe.
--:   3. CALLED WITH THE ARGUMENT SHAPE VANILLA PASSES, derived from vanilla's own call
--:      sites rather than guessed -- zero-arg, an object id, or a faction id.
--: UI-widget names (Frame/Widget/Render/Texture/Mouse/Cursor/Row/Cell/...) are filtered
--: out: they are not world data and poking the live UI is not recon.
--:
--: ⚠ THE LIST IS HARDCODED ON PURPOSE. A verb that calls a caller-supplied name would be
--: an arbitrary-execution primitive wearing a READ label -- the one thing this mod
--: must never become. Adding a name is a code change, reviewed like any other.
--:
--: Results are SUMMARISED, never dumped: a table becomes its length, a string its first
--: 40 characters. Some of these return the whole extension list or every stat id, and an
--: over-long reply tears the pipe down rather than truncating.
--: ONE-ARG-PAIR taking a CONTAINER, non-owner-scoped. The four the engine EXPORTS and
--: vanilla NEVER CALLS, plus the one it does.
--:
--: Attestation, MEASURED over 81 vanilla ui lua files -- and it is UNEVEN, which is the
--: whole reason this list is separate and the results are RECORDED, NOT CONCLUDED:
--:     GetContainedStations              7 vanilla sites, shape (container, boolean)
--:     GetContainedShips                 0 sites -- signature INFERRED from its sibling
--:     GetContainedShipsByOwner          0 sites -- shape (owner, container) inferred
--:     GetContainedSpacesByOwner         0 sites -- same
--:     GetContainedBuildStoragesByOwner  1 site,  shape (owner, container)
--:
--: "It exists in _G" is not evidence that calling it is safe, and it is certainly not
--: evidence about its ARGUMENTS -- F88 was exactly a wrong-argument call that answered
--: `OK number:0`. So every call here is pcall'd, an ERR is a RECORDED RESULT rather than
--: a failure, and nothing downstream is built on any of it in this build.
local CENSUS_CONTAINER = { "GetContainedStations", "GetContainedShips" }
local CENSUS_BYOWNER = { "GetContainedStationsByOwner", "GetContainedShipsByOwner",
                         "GetContainedSpacesByOwner", "GetContainedBuildStoragesByOwner" }

--: censusprobe <sector> [faction] [--hidden]
--:
--: ★ THE QUESTION. Our enumeration verbs are owner-scoped and the manifest says so:
--: ownerless objects are invisible to ANY owner query, hidden factions are opt-in, and
--: ~93% of names come back Unknown. MEASURED 2026-08-31 over all 803 engine functions:
--: there is NO ownerless-object enumerator, so that first cause is PERMANENT. This verb
--: attacks the SECOND cause only -- do the non-owner-scoped calls see factions the
--: per-faction walk misses?
--:
--: ★ IT COMPARES SETS, NOT COUNTS. Two totals can agree by coincidence while naming
--: different objects, and a count is exactly the aggregate a real difference hides in.
--: So it reports |A|, |B|, |A-B| and |B-A| per pair. A row of four zeroes is a real
--: answer; two equal totals alone would not be.
verbs.censusprobe = function(seq, ...)
    local opts, pos = split_flags(...)
    local sector = pos[1]
    if sector == nil then
        reply(seq, "ERR", "censusprobe needs a sector token, the one `player` reports "
                          .. "as sectorid, or - for the whole galaxy")
        return
    end
    if sector == "-" or sector == "galaxy" then sector = nil end

    -- F85: an unrecognised container is SILENTLY IGNORED and you get the whole galaxy
    -- back labelled as your sector. Same guard as `objects`, called not re-written.
    local bad = bad_sector_token(sector)
    if bad then reply(seq, "ERR", bad) return end

    local container = nil
    if sector ~= nil then container = ConvertStringToLuaID(tostring(sector)) end

    local out = {}
    local function count_of(v)
        if type(v) ~= "table" then return nil end
        local n = 0
        for _ in pairs(v) do n = n + 1 end
        return n
    end
    -- Keeps the OBJECT beside its key. A set of `tostring` handles can be diffed but
    -- not followed; to say WHICH objects differ we have to hand back real ids, and
    -- those must go through `issue` so a later `component` call will accept them.
    local function idset(v)
        local s, objs, n = {}, {}, 0
        if type(v) ~= "table" then return s, 0, objs end
        for _, o in ipairs(v) do
            local k = tostring(o)
            if not s[k] then s[k] = true objs[k] = o n = n + 1 end
        end
        return s, n, objs
    end
    local function only_in(a, b)
        local n = 0
        for k in pairs(a) do if not b[k] then n = n + 1 end end
        return n
    end

    -- 1. the container-scoped pair, BOTH booleans. Vanilla only ever passes `true`, so
    --    `false` is a GUESS and is labelled one in the output.
    local sets, objmaps = {}, {}
    for _, name in ipairs(CENSUS_CONTAINER) do
        -- env FIRST, _G as the fallback. `probe` and `containerprobe` deliberately
        -- consult BOTH, with a comment saying a single-namespace check "would report
        -- a confident absence for a symbol that is reachable" -- these sites ignored
        -- that. Written as `or` on purpose: it is a strict SUPERSET of the old
        -- lookup, so it can only ever find MORE, and the offline harness (Lua 5.5,
        -- getfenv nil) exercises exactly the old path. A branch no test can reach
        -- must not be able to take anything away.
        local env = (type(getfenv) == "function") and getfenv(1) or _G
        local fn = env[name] or _G[name]
        if type(fn) ~= "function" then
            out[#out + 1] = name .. "|ABSENT|not a function in this chunk"
        else
            for _, flag in ipairs({ true, false }) do
                local ok, res = pcall(fn, container, flag)
                if not ok then
                    out[#out + 1] = name .. "(container," .. tostring(flag)
                        .. ")|RAISED|" .. (utf8_prefix(tostring(res), 60):gsub("[|\t\n\r]", " "))
                else
                    local s, n, objs = idset(res)
                    objmaps[name .. (flag and ":true" or ":false")] = objs
                    -- BOTH sets are kept. The first version stored only the `true` one
                    -- and MEASURED 2026-08-31 that `true` is the RESTRICTIVE argument
                    -- (stations 1 vs 19, ships 23 vs 169) -- so the comparison was
                    -- running against a filtered set and answering a narrower question
                    -- than the one available. Keeping one set was the bug.
                    sets[name .. (flag and ":true" or ":false")] = s
                    out[#out + 1] = name .. "(container," .. tostring(flag) .. ")|OK|n="
                        .. n .. " type=" .. type(res)
                        .. (flag and " [vanilla-attested arg]" or " [GUESSED arg]")
                end
            end
        end
    end

    -- 2. the owner-scoped baseline, summed per faction, as a SET
    -- An explicit faction scopes the owner-scoped side to one owner, exactly as
    -- `objects <sector> [class] [faction]` does. Useful live (one faction is a much
    -- cheaper comparison) and it is the seam the tests use, so no test-only branch has
    -- to exist in shipped code.
    local factions = pos[2] and { pos[2] }
                     or faction_list(opts.hidden and true or false)
    local owner_sets, failed = {}, {}
    for _, name in ipairs(CENSUS_BYOWNER) do
        -- env FIRST, _G as the fallback. `probe` and `containerprobe` deliberately
        -- consult BOTH, with a comment saying a single-namespace check "would report
        -- a confident absence for a symbol that is reachable" -- these sites ignored
        -- that. Written as `or` on purpose: it is a strict SUPERSET of the old
        -- lookup, so it can only ever find MORE, and the offline harness (Lua 5.5,
        -- getfenv nil) exercises exactly the old path. A branch no test can reach
        -- must not be able to take anything away.
        local env = (type(getfenv) == "function") and getfenv(1) or _G
        local fn = env[name] or _G[name]
        if type(fn) ~= "function" then
            out[#out + 1] = name .. "|ABSENT|not a function in this chunk"
        else
            local acc, nfail = {}, 0
            for _, fac in ipairs(factions) do
                local ok, lst = pcall(fn, fac, container)
                if ok and type(lst) == "table" then
                    for _, o in ipairs(lst) do acc[tostring(o)] = true end
                else
                    nfail = nfail + 1
                end
            end
            local n = 0
            for _ in pairs(acc) do n = n + 1 end
            owner_sets[name] = acc
            failed[name] = nfail
            out[#out + 1] = name .. "(each faction, container)|OK|distinct=" .. n
                .. " factions=" .. #factions .. " faction_failures=" .. nfail
        end
    end

    -- 2b. DECLARE THE COST. Unlike compare and containerprobe there is no
    -- per-object engine call here to bound: each entry above is ONE call that
    -- returns a whole list, so the only way to make this cheaper is to ask for
    -- less -- which is the operator's call, not ours, and `censusprobe -` is
    -- them asking for the galaxy on purpose. What was missing is that the cost
    -- was nowhere on the wire: a bare `censusprobe -` is 4 + 4x#factions
    -- enumerations, every one galaxy-wide, inside the single frame pipes.lua
    -- drains the FIFO in. An undeclared bound is the thing this file keeps
    -- getting wrong; declaring it is the fix the finding actually asked for.
    --
    -- Counted from the SETS rather than from len(CENSUS_*), so an ABSENT or
    -- RAISING function lowers the number instead of being billed for work it
    -- never did -- and so the row cannot drift from the rows above it.
    local n_enum, n_objects = 0, 0
    for _, s in pairs(sets) do
        n_enum = n_enum + 1
        for _ in pairs(s) do n_objects = n_objects + 1 end
    end
    for _, s in pairs(owner_sets) do
        n_enum = n_enum + #factions
        for _ in pairs(s) do n_objects = n_objects + 1 end
    end
    out[#out + 1] = "COST|OK|enumerations=" .. n_enum
        .. " objects_touched=" .. n_objects
        .. " factions=" .. #factions
        .. " scope=" .. (container == nil and "galaxy" or "container")
        .. " [one frame; each enumeration is a single engine call]"

    -- 3. THE COMPARISON, per item. This is the row the verb exists for.
    local PAIRS = {}
    for _, base in ipairs({ { "GetContainedStations", "GetContainedStationsByOwner" },
                            { "GetContainedShips", "GetContainedShipsByOwner" } }) do
        for _, fl in ipairs({ "true", "false" }) do
            PAIRS[#PAIRS + 1] = { base[1] .. ":" .. fl, base[2], base[1] }
        end
    end
    for _, p in ipairs(PAIRS) do
        local a, b = sets[p[1]], owner_sets[p[2]]
        if #factions == 0 then
            -- ★ THE COMPARISON IS VACUOUS WITHOUT FACTIONS, AND IT FAILS TOWARDS THE
            -- CONCLUSION WE ARE HUNTING. With an empty faction list the owner-scoped
            -- side is empty by construction, so every container-scoped id lands in
            -- `container_only` and the row reads "the non-owner call sees N objects the
            -- per-faction walk missed" -- the exact finding this verb exists to test
            -- for, manufactured out of nothing. Found by a test that went red for the
            -- right reason.
            out[#out + 1] = "CMP:" .. p[1] .. "-vs-" .. p[2]
                .. "|SKIPPED|faction list is EMPTY, so the owner-scoped side is "
                .. "vacuously empty and ANY difference would be an artifact of that, "
                .. "not a finding"
        elseif a == nil or b == nil then
            out[#out + 1] = "CMP:" .. p[1] .. "-vs-" .. p[2]
                .. "|SKIPPED|one side did not return a table"
        else
            local na, nb = 0, 0
            for _ in pairs(a) do na = na + 1 end
            for _ in pairs(b) do nb = nb + 1 end
            -- NAME the container-only objects, not just count them. An unexplained
            -- remainder is a lead however small, and "4 ships no faction owns" is a
            -- number; "these 4 ids" is something you can follow with `component`.
            -- Bounded, and it says how many it withheld.
            local names, extra = {}, 0
            for k in pairs(a) do
                if not b[k] then
                    if #names < 12 and objmaps[p[1]] and objmaps[p[1]][k] then
                        names[#names + 1] = issue(objmaps[p[1]][k])
                    else extra = extra + 1 end
                end
            end
            out[#out + 1] = "CMP:" .. p[1] .. "-vs-" .. p[2] .. "|OK|"
                .. "container_only=" .. only_in(a, b)
                .. " owner_only=" .. only_in(b, a)
                .. " n_container=" .. na .. " n_owner=" .. nb
                .. (#names > 0 and (" container_only_ids=" .. table.concat(names, ",")
                    .. (extra > 0 and (" +" .. extra .. "-unnamed") or "")) or "")
        end
    end

    -- Is the `true` reply a strict SUBSET of the `false` reply? That is the shape a
    -- pure FILTER would have. If it is not a subset, `true` is selecting a different
    -- population rather than narrowing one, and no filter story explains it.
    for _, base in ipairs({ "GetContainedStations", "GetContainedShips" }) do
        local t, f = sets[base .. ":true"], sets[base .. ":false"]
        if t ~= nil and f ~= nil then
            local nt, outside = 0, 0
            for k in pairs(t) do
                nt = nt + 1
                if not f[k] then outside = outside + 1 end
            end
            out[#out + 1] = "SUBSET:" .. base .. "|OK|true_n=" .. nt
                .. " true_not_in_false=" .. outside
                .. (outside == 0 and " [true IS a subset of false -- filter-shaped]"
                                 or " [NOT a subset -- different population, not a filter]")
        end
    end

    local shown, used, capped = 0, 0, false
    local rows = {}
    for _, r in ipairs(out) do
        if used + #r + 1 > ROW_BUDGET then capped = true
        else rows[#rows + 1] = r used = used + #r + 1 shown = shown + 1 end
    end
    table.insert(rows, 1, "shown=" .. shown .. " rows=" .. #out
        .. " sector=" .. (sector and header_safe(sector) or "galaxy")
        .. " factions=" .. #factions
        .. " hidden=" .. (opts.hidden and "yes" or "no")
        .. (capped and (" CAPPED=yes omitted=" .. (#out - shown)) or " CAPPED=no"))
    reply(seq, "OK", table.concat(rows, "\t"))
end

verbs.recon = function(seq, ...)
    local opts, pos = split_flags(...)
    local id_a, id_b, faction = pos[1], pos[2], pos[3]
    local out, nok, nraise, nabsent, nmacro = {}, 0, 0, 0, 0

    --: `--contents` is OPT-IN, because 109 recorded calls in recon-20260830-211605.tsv
    --: were taken with the shape-only summary: changing the default would make this run
    --: and that fixture non-comparable while looking like the same command. It renders
    --: ONE level; `--deep` adds exactly one more (GetUnitStorageData's nested tables).
    --: Bounds, sanitising and sort order live in `render_table`, shared with `macro`.

    local function summarize(v)
        local t = type(v)
        if t == "nil" then return "nil" end
        if t == "table" then
            local keys = 0
            for _ in pairs(v) do keys = keys + 1 end
            local s = "table[#" .. #v .. ",keys=" .. keys .. "]"
            if opts.contents then s = s .. render_table(v, opts.deep and 2 or 1) end
            return s
        end
        if t == "string" then
            return "string:" .. #v .. ":" .. (utf8_prefix(v, 40):gsub("[|\t\n\r]", " "))
        end
        if t == "boolean" or t == "number" then return t .. ":" .. tostring(v) end
        return t
    end

    local function try(label, name, ...)
        -- env FIRST, _G as the fallback. `probe` and `containerprobe` deliberately
        -- consult BOTH, with a comment saying a single-namespace check "would report
        -- a confident absence for a symbol that is reachable" -- these sites ignored
        -- that. Written as `or` on purpose: it is a strict SUPERSET of the old
        -- lookup, so it can only ever find MORE, and the offline harness (Lua 5.5,
        -- getfenv nil) exercises exactly the old path. A branch no test can reach
        -- must not be able to take anything away.
        local env = (type(getfenv) == "function") and getfenv(1) or _G
        local fn = env[name] or _G[name]
        if type(fn) ~= "function" then
            nabsent = nabsent + 1
            out[#out + 1] = label .. "|ABSENT|not a function in this chunk"
            return
        end
        local ok, a, b = pcall(fn, ...)
        if not ok then
            nraise = nraise + 1
            out[#out + 1] = label .. "|RAISED|" .. (utf8_prefix(tostring(a), 70):gsub("[|\t\n\r]", " "))
        else
            nok = nok + 1
            local s = summarize(a)
            if b ~= nil then s = s .. " +" .. summarize(b) end
            out[#out + 1] = label .. "|OK|" .. s
        end
    end

    for _, n in ipairs(RECON_ZERO) do try(n .. "()", n) end

    -- Object-id probes run against ids THIS SESSION ISSUED, for the same reason
    -- `component` refuses a foreign id: vanilla never passes one, so the engine's
    -- behaviour on a fabricated id is unknown in both directions.
    for _, id_str in ipairs({ id_a, id_b }) do
        if id_str ~= nil and id_str ~= "" then
            if not issued_ids[id_str] then
                out[#out + 1] = "id:" .. tostring(id_str) .. "|SKIPPED|not issued by this session"
            else
                local cok, id = pcall(ConvertStringTo64Bit, id_str)
                if cok and id ~= nil then
                    for _, n in ipairs(RECON_OBJ) do
                        try(n .. "(" .. id_str .. ")", n, id)
                    end
                    -- F88: the MACRO-arg probes need a macro, and the only vanilla-
                    -- attested way to get one from an object is menu_map.lua:10468.
                    -- If that hop fails we record the FAILURE and skip -- we do NOT
                    -- fall back to passing the object id, which is the bug this fixes.
                    local mok, macro = pcall(GetComponentData, id, "macro")
                    if mok and type(macro) == "string" and macro ~= "" then
                        nmacro = nmacro + 1
                        for _, n in ipairs(RECON_MACRO) do
                            try(n .. "('" .. macro .. "')", n, macro)
                        end
                    else
                        out[#out + 1] = "macro-for:" .. id_str
                            .. "|SKIPPED|GetComponentData(id,'macro') gave "
                            .. type(macro) .. " -- MACRO probes not run for this id"
                    end
                end
            end
        end
    end

    local fac = faction or "argon"
    for _, n in ipairs(RECON_FAC) do try(n .. "('" .. fac .. "')", n, fac) end

    local shown, used, capped = 0, 0, false
    local rows = {}
    for _, r in ipairs(out) do
        if used + #r + 1 > ROW_BUDGET then
            capped = true
        else
            rows[#rows + 1] = r
            used = used + #r + 1
            shown = shown + 1
        end
    end
    local header = "shown=" .. shown .. " called=" .. #out
                   .. " ok=" .. nok .. " raised=" .. nraise .. " absent=" .. nabsent
                   .. " zero=" .. #RECON_ZERO .. " perobj=" .. #RECON_OBJ
                   .. " permacro=" .. #RECON_MACRO .. " macros_resolved=" .. nmacro
                   .. " contents=" .. (opts.contents and "yes" or "no")
                   .. " deep=" .. (opts.deep and "yes" or "no")
                   .. " faction=" .. fac
                   .. (capped and (" CAPPED=yes omitted=" .. (#out - shown)) or " CAPPED=no")
    table.insert(rows, 1, header)
    reply(seq, "OK", table.concat(rows, "\t"))
end

--: THE FFI CENSUS. `globals` walks _G, and vanilla declares 2,066 C functions in ffi.cdef
--: that _G never shows (SPEC_2026-09-07) -- so a `globals` negative covered ~30% of the
--: engine surface. This asks ffi.C about NAMED symbols; `x4live ffi-census` supplies the
--: names from vanilla's own cdef blocks and batches them.
--:  * INDEX ONLY, NEVER CALL. `C[name]` resolves a symbol and marshals nothing.
--:  * NEVER DECLARE. MEASURED with LuaJIT 2.1 (lupa, 2026-09-14): a second ffi.cdef of a
--:    name with a DIFFERENT signature is silently ignored and the FIRST wins. All UI lua
--:    shares one ffi state, so a placeholder declared here before a vanilla file's real one
--:    would corrupt every later call to that function.
--:  * CLASSIFIED ON LuaJIT's OWN TEXT: "missing declaration for symbol" = nothing in this VM
--:    declares it; "cannot resolve symbol" = declared, not resolvable in the process. On
--:    Windows LuaJIT searches the executable AND the libraries it loaded, so `exported` is
--:    "resolvable in the game process", not strictly "the engine". Anything else is `other`,
--:    quoted -- never folded into either.
--:  * BOUNDED: FFISYMS_MAX_NAMES names of at most FFISYMS_MAX_NAME_LEN bytes, so the worst
--:    reply (every row `other` with a 60-byte quote) stays far under ROW_BUDGET.
local FFISYMS_MAX_NAMES = 150
local FFISYMS_MAX_NAME_LEN = 100

verbs.ffisyms = function(seq, ...)
    local names = { ... }
    if not ffi_ok or ffi == nil then
        reply(seq, "ERR", "ffi is not available in this environment -- no symbol can be censused")
        return
    end
    if #names == 0 then
        reply(seq, "ERR", "ffisyms needs one or more symbol names")
        return
    end
    if #names > FFISYMS_MAX_NAMES then
        reply(seq, "ERR", "ffisyms takes at most " .. FFISYMS_MAX_NAMES .. " names per call ("
              .. #names .. " sent) -- batch them, so the reply stays bounded")
        return
    end
    local lib = ffi.C
    local rows = {}
    local n = { exported = 0, notexported = 0, undeclared = 0, other = 0, invalid = 0 }
    for _, name in ipairs(names) do
        if #name > FFISYMS_MAX_NAME_LEN or not name:match("^[%a_][%w_]*$") then
            -- Not indexed at all, and not echoed: the name is what failed validation.
            n.invalid = n.invalid + 1
            rows[#rows + 1] = "<invalid>|invalid"
        else
            local ok, sym = pcall(function() return lib[name] end)
            if ok then
                n.exported = n.exported + 1
                rows[#rows + 1] = name .. "|exported|" .. type(sym)
            else
                local msg = tostring(sym)
                if msg:find("missing declaration for symbol", 1, true) then
                    n.undeclared = n.undeclared + 1
                    rows[#rows + 1] = name .. "|undeclared"
                elseif msg:find("cannot resolve symbol", 1, true) then
                    n.notexported = n.notexported + 1
                    rows[#rows + 1] = name .. "|notexported"
                else
                    n.other = n.other + 1
                    rows[#rows + 1] = name .. "|other|" .. (utf8_prefix(msg, 60):gsub("[|\t\n\r]", " "))
                end
            end
        end
    end
    local header = "asked=" .. #names .. " exported=" .. n.exported
                   .. " notexported=" .. n.notexported .. " undeclared=" .. n.undeclared
                   .. " other=" .. n.other .. " invalid=" .. n.invalid
    table.insert(rows, 1, header)
    reply(seq, "OK", table.concat(rows, "\t"))
end

verbs.globals = function(seq, prefix, page)
    local want = nil
    if prefix ~= nil and prefix ~= "" and prefix ~= "-" then
        want = tostring(prefix):lower()
    end
    local pg = tonumber(page) or 1
    if pg < 1 then pg = 1 end

    local all, ntotal, nfun = {}, 0, 0
    local ok, err = pcall(function()
        for k, v in pairs(_G) do
            ntotal = ntotal + 1
            local name = tostring(k)
            local t = type(v)
            if t == "function" then nfun = nfun + 1 end
            -- Substring, not prefix: the engine names are not consistently prefixed
            -- (GetSectors vs IsValidComponent vs ConvertStringToLuaID), so an anchored
            -- match would hide most of what a caller is hunting for.
            if want == nil or name:lower():find(want, 1, true) ~= nil then
                all[#all + 1] = name .. "|" .. t
            end
        end
    end)
    if not ok then
        reply(seq, "ERR", "iterating _G raised: " .. tostring(err))
        return
    end

    table.sort(all)

    local pages, cur, used = {}, {}, 0
    for _, row in ipairs(all) do
        if used + #row + 1 > ROW_BUDGET then
            pages[#pages + 1] = cur
            cur, used = {}, 0
        end
        cur[#cur + 1] = row
        used = used + #row + 1
    end
    if #cur > 0 or #pages == 0 then pages[#pages + 1] = cur end

    if pg > #pages then
        -- ABSENT, not ERR: the question was well formed and the answer is "no such page".
        reply(seq, "ABSENT", "page " .. pg .. " of " .. #pages .. ": no such page")
        return
    end

    local header = "shown=" .. #pages[pg] .. " matched=" .. #all
                   .. " enumerated=" .. ntotal .. " functions=" .. nfun
                   .. " page=" .. pg .. " pages=" .. #pages
                   .. " filter=" .. (want or "-")
    -- TAB-separated with the header as field 1 -- the house convention every other
    -- verb uses, and what `Reply.fields` splits on. A newline here would collapse
    -- the whole reply into ONE field, and every caller would still appear to
    -- "work" while reading the rows as part of the header.
    local page_rows = {}
    for i, r in ipairs(pages[pg]) do page_rows[i] = r end
    table.insert(page_rows, 1, header)
    reply(seq, "OK", table.concat(page_rows, "\t"))
end

--: Answers, in ONE reply, every open question about the galaxy-enumeration primitives.
--: They are all BARE GLOBALS in vanilla (MEASURED: 8 of 8 GetClusters call sites and 8
--: of 8 GetSectors call sites use the bare form, zero use the ffi `C.` form), but
--: vanilla using a symbol is not evidence that OUR chunk can reach it -- reachability is
--: decided by ui.xml registration and has to be measured, not inferred.
--:
--: Three things are deliberately asked BOTH ways rather than picked:
--:   * GetClusters(bool) -- every vanilla site passes true and the argument is
--:     undocumented, so ask true AND false and report both counts.
--:   * GetContainedStations(container, bool) -- same, second argument undocumented.
--:   * whether GetSectors is knowledge-limited -- vanilla wrapper is named
--:     prepareKnownSectors, which is a HINT and not evidence. Counting isknown over
--:     every sector it returns settles it in one query.
verbs.galaxyprobe = function(seq)
    local out = {}
    local function note(k, v) out[#out + 1] = tostring(k) .. "=" .. tostring(v) end

    -- 1. Existence and type. READ, never called.
    local NAMES = { "GetClusters", "GetSectors", "GetContainedStations",
                    "GetContainedObjectsByOwner", "GetSectorsByOwner",
                    "GetContainedBuildStoragesByOwner", "GetSectorControlStation",
                    "GetComponentData", "ConvertStringToLuaID", "ConvertIDTo64Bit" }
    -- REPORTED from the same namespace it ACTS on. This read `_G[n]` while the calls
    -- below use the bare (environment) symbol, so it described one namespace and used
    -- the other -- the sharpest form of the same defect.
    local genv = (type(getfenv) == "function") and getfenv(1) or _G
    for _, n in ipairs(NAMES) do note("type." .. n, type(genv[n] or _G[n])) end

    -- 2. The undocumented boolean on GetClusters.
    local function n_clusters(flag)
        if type(GetClusters) ~= "function" then return "?" end
        local cok, r = pcall(GetClusters, flag)
        if not cok or type(r) ~= "table" then return "?" end
        return #r
    end
    note("clusters.arg_true", n_clusters(true))
    note("clusters.arg_false", n_clusters(false))

    -- 3. Walk clusters -> sectors, the vanilla idiom verbatim
    --    (menu_map.lua:29492, menu.prepareKnownSectors).
    local sectors = {}
    local cluster_fail, sector_fail = 0, 0
    if type(GetClusters) == "function" and type(GetSectors) == "function" then
        local cok, cl = pcall(GetClusters, true)
        if cok and type(cl) == "table" then
            for _, c in ipairs(cl) do
                local sok, ss = pcall(GetSectors, c)
                if sok and type(ss) == "table" then
                    for _, s in ipairs(ss) do sectors[#sectors + 1] = s end
                else
                    sector_fail = sector_fail + 1
                end
            end
        else
            cluster_fail = 1
        end
    else
        cluster_fail = 1
    end
    -- COUNTED, not dropped. The cluster->sector loop had NO counter at all: a failing
    -- `pcall(GetSectors, c)` was discarded, so with every cluster raising the reply was
    -- `status OK`, `sectors.total=0`, `isknown_undecidable=0` -- the field that exists
    -- to say "could not tell" reading 0 while 100% of the walk failed, because the
    -- failure happens one level ABOVE where it was counted. Every other verb in this
    -- file counts and NAMES its failures (meta.failed, nunreadable, nunclassified).
    note("clusters.failed", cluster_fail)
    note("sectors.failed", sector_fail)
    note("sectors.total", #sectors)
    note("sectors.elemtype", (#sectors > 0) and type(sectors[1]) or "?")

    -- 4. IS GetSectors KNOWLEDGE-LIMITED? This is the question everything turns on.
    --    isknown=false rows PRESENT means it is not: it hands back undiscovered sectors
    --    too, and only their LABELS are player-knowledge.
    local kt, kf, ku = 0, 0, 0
    for _, s in ipairs(sectors) do
        local gok, k = pcall(GetComponentData, s, "isknown")
        if not gok then ku = ku + 1
        elseif k == true then kt = kt + 1
        elseif k == false then kf = kf + 1
        else ku = ku + 1 end
    end
    note("sectors.isknown_true", kt)
    note("sectors.isknown_false", kf)
    note("sectors.isknown_undecidable", ku)

    -- 5. The undocumented boolean on GetContainedStations, on the first sector we have.
    if #sectors > 0 and type(GetContainedStations) == "function" then
        local function n_st(flag)
            local sok, r = pcall(GetContainedStations, sectors[1], flag)
            if not sok or type(r) ~= "table" then return "?" end
            return #r
        end
        note("stations.arg_true", n_st(true))
        note("stations.arg_false", n_st(false))
    else
        note("stations.arg_true", "?")
        note("stations.arg_false", "?")
    end

    -- 6. A bounded per-sector sample, so the counts above have items behind them.
    --    ROWS, not aggregates: an aggregate cannot show that ids and NAMES behave
    --    differently, which is the finding this is looking for.
    local rows, used = {}, 0
    for _, s in ipairs(sectors) do
        local nm, ow, kn = "?", "?", "?"
        local gok, a, b, c2 = pcall(GetComponentData, s, "name", "owner", "isknown")
        if gok then nm, ow, kn = tostring(a), tostring(b), tostring(c2) end
        local nst = "?"
        if type(GetContainedStations) == "function" then
            local sok, r = pcall(GetContainedStations, s, true)
            if sok and type(r) == "table" then nst = #r end
        end
        local safe = nm:gsub("|", "/")
        local row = tostring(s) .. "|" .. safe .. "|" .. ow .. "|" .. kn .. "|" .. tostring(nst)
        if used + #row + 1 > (ROW_BUDGET - 1024) then break end
        rows[#rows + 1] = row
        used = used + #row + 1
    end
    note("sample.shown", #rows)

    table.insert(rows, 1, table.concat(out, " "))
    reply(seq, "OK", table.concat(rows, "\t"))
end

-- --------------------------------------------------------------------------- --
-- read loop
-- --------------------------------------------------------------------------- --

-- The ONE place Schedule_Read is called. Everything that needs the channel armed
-- goes through here, so "armed" cannot drift from reality.
local function arm_read()
    if pipes == nil then
        DebugError("X4TOOLKIT_LIVE cannot arm: the named pipes api did not load")
        return
    end
    local ok, err = pcall(pipes.Schedule_Read, PIPE, on_message, true)
    if ok then
        armed = true
    else
        armed = false
        DebugError("X4TOOLKIT_LIVE Schedule_Read failed: " .. tostring(err))
        -- A FAULT, not idleness: the retry below is fast, and once an arm succeeds the
        -- idle back-off starts again from fast rather than from where it had grown to.
        rearm_delay = REARM_DELAY
        -- Until 2026-08-29 this branch was TERMINAL: a single failed arm killed the
        -- channel until the next reload, with only one log line to say so. Route it
        -- back through the deferred re-arm instead -- the same path a disconnect
        -- takes, so a transient failure recovers on its own. Deferred, never
        -- synchronous: calling Schedule_Read from here during a Close_Pipe drain is
        -- the hang documented below.
        schedule_rearm()
    end
end

-- ⚠ THE RE-ARM MUST BE DEFERRED, AND THIS IS NOT A STYLE CHOICE.
--
-- `Close_Pipe` (pipes.lua:440) drains the read FIFO in a `while not FIFO.Is_Empty`
-- loop, calling every callback with "ERROR", and only sets `pipes[name] = nil`
-- AFTERWARDS. `Declare_Pipe` reuses existing state, so a Schedule_Read called
-- synchronously from inside that callback writes into THE VERY FIFO BEING DRAINED --
-- the loop never terminates and the game HANGS on the UI thread.
--
-- So we hand the re-arm to the next update instead. Shape copied verbatim from an
-- installed mod that loads its lua exactly as we do (kuertee_additional_agent_actions
-- /ui/*.lua:142): Helper as a bare global, blockinput FALSE, and an ABSOLUTE time.
-- ⚠ blockinput=true calls C.SetAllUIInputIgnored(true) and freezes the player's input
-- until the callback fires. Never pass true here.
--
-- The delay doubles as the throttle: with no server listening the cycle is
-- re-arm -> Close_Pipe -> ERROR -> re-arm, which at frame rate would add ~60 lines a
-- second to debug.txt -- and debug.txt is x4debug's input, so that would corrupt an
-- unrelated instrument.
schedule_rearm = function(delay)
    delay = delay or REARM_DELAY
    if type(Helper) ~= "table"
       or type(Helper.addDelayedOneTimeCallbackOnUpdate) ~= "function" then
        -- Refuse rather than re-arm synchronously. A hung game is far worse than a
        -- channel that needs a reload, and this says WHICH it is.
        DebugError("X4TOOLKIT_LIVE cannot re-arm: Helper.addDelayedOneTimeCallbackOnUpdate "
                   .. "is absent. The channel is now DISARMED until the next reload.")
        return
    end
    -- The deadline is ABSOLUTE (compared against getElapsedTime), so without a clock
    -- reading there is no deadline to build. Until 2026-08-29 a failed read left
    -- now = 0, making the deadline 2.0 -- a time long past on any running game. The
    -- callback then fired on the NEXT FRAME and the 2s throttle silently vanished,
    -- turning the no-server cycle into ~60 re-arms a second, each able to log. That
    -- floods debug.txt, which is x4debug's INPUT: a degraded channel would have
    -- corrupted an unrelated instrument. Refuse loudly instead of degrading quietly.
    local now, have_clock = 0, false
    if type(getElapsedTime) == "function" then
        local ok, t = pcall(getElapsedTime)
        if ok and type(t) == "number" then now, have_clock = t, true end
    end
    if not have_clock then
        DebugError("X4TOOLKIT_LIVE cannot re-arm: getElapsedTime is unavailable, so the "
                   .. "throttle cannot be honoured and an unthrottled re-arm would "
                   .. "flood debug.txt. The channel is DISARMED until the next reload.")
        return
    end
    Helper.addDelayedOneTimeCallbackOnUpdate(function() arm_read() end, false,
                                             now + delay)
end

-- --------------------------------------------------------------------------- --
-- the PAUSE verbs -- `pausestate` (a read) and `pause` / `unpause` (the writes)
-- --------------------------------------------------------------------------- --

--: The engine's pause verdict, or nil plus WHY it could not be read. Never a guess: a
--: non-boolean is unreadable, because `1 == true` is false in lua and would otherwise
--: read as "not paused" and let a write act on it.
local function read_paused()
    if not pause_cdef_ok then
        return nil, "the IsGamePaused ffi declaration did not load"
    end
    local ok, v = pcall(function() return ffi.C.IsGamePaused() end)
    if not ok then return nil, "C.IsGamePaused raised: " .. tostring(v) end
    if type(v) ~= "boolean" then
        return nil, "C.IsGamePaused answered a " .. type(v) .. ", not a boolean"
    end
    return v
end

local function pause_owner(paused)
    if paused == nil then return "unknown" end
    if not paused then return "none" end
    return our_pause and "us" or "other"
end

--: Resolved at CALL time from the same namespace recon uses, so a missing global is a
--: refusal that names it rather than a raise inside the write.
local function engine_call(name)
    local env = (type(getfenv) == "function") and getfenv(1) or _G
    local fn = env[name] or _G[name]
    if type(fn) ~= "function" then return nil end
    return fn
end

--: ONE implementation for both writes, so their guards cannot drift apart.
--: GUARD ORDER IS DELIBERATE, and each clause shadows the ones after it -- which is why
--: each has its own test rather than one test for "it refuses":
--:   args -> unreadable -> no-engine-call -> (a stale ownership flag is cleared) ->
--:   already-paused | not-paused -> not-ours -> ACT -> raised | unverified | disagree | ok
local function pause_write(seq, verb, want, engine_name, nargs)
    local t = { verb = verb, want = want, acted = false, ownerreset = false }
    local function finish(status, reason, prose)
        t.reason = reason
        -- `agree` is a fact about an ACT. A refusal changed nothing, so it has nothing
        -- to agree with, and `agree=yes` there would read as success to a naive caller.
        if t.acted and t.after ~= nil then t.agree = (t.after == t.want) end
        t.owner = pause_owner(t.after)
        DebugError("X4TOOLKIT_LIVE WRITE " .. verb .. " result: reason=" .. reason
                   .. " acted=" .. yn(t.acted) .. " before=" .. tri(t.before)
                   .. " after=" .. tri(t.after))
        reply(seq, status, write_advisory(t) .. "\t" .. prose)
    end

    if nargs > 0 then
        return finish("ERR", "args", verb .. " takes no arguments and was given " .. nargs
            .. ". A write verb has no argument surface on purpose: nothing a caller sends "
            .. "can change what it does. Nothing was changed.")
    end

    local before, why = read_paused()
    t.before, t.after = before, before
    if before == nil then
        return finish("ERR", "unreadable", verb .. " refused: the pause state cannot be "
            .. "read back (" .. tostring(why) .. "), and a write that cannot be verified "
            .. "is never performed. Nothing was changed.")
    end

    local fn = engine_call(engine_name)
    if fn == nil then
        return finish("ERR", "no-engine-call", verb .. " refused: the engine global "
            .. engine_name .. " is not a function in this chunk. Nothing was changed.")
    end

    if our_pause and not before then
        -- The engine shows no pause, so the one we made has been undone by someone
        -- else. Holding on to the flag would let a later pause of THEIRS read as ours.
        our_pause = false
        t.ownerreset = true
    end

    if before == want then
        if want then
            return finish("ERR", "already-paused", "the game is already paused (owner="
                .. pause_owner(before) .. "). This channel never stacks a pause on top "
                .. "of another. Nothing was changed.")
        end
        return finish("ERR", "not-paused",
            "the game is not paused, so there is nothing to unpause. Nothing was changed.")
    end

    if (not want) and not our_pause then
        return finish("ERR", "not-ours", "the game is paused, but this channel does not "
            .. "hold that pause, so it will not undo it. Either the player, a menu or another "
            .. "mod paused; or an earlier `unpause` from this channel could not confirm the "
            .. "game resumed and gave up the claim; or the UI reloaded (alt-enter, loading a "
            .. "save) after we paused -- a reload re-creates this chunk, which then cannot "
            .. "prove the pause is ours while the pause itself survives. Unpause it in game. "
            .. "Nothing was changed.")
    end

    t.acted = true
    local ok, err = pcall(fn)
    local after, awhy = read_paused()
    t.after = after
    -- Ownership after ACTING. A pause is claimed only when the read-back shows it
    -- landed. An unpause ATTEMPT always releases the claim, whatever the read-back:
    -- vanilla's menus each call Pause() on show and Unpause() on close without
    -- checking, so the engine either COUNTS pauses or lets them undo each other --
    -- UNMEASURED which. If it counts, a pause still holding after our Unpause() is
    -- somebody else's, and a retry would undo it. Never undo what we cannot prove we made.
    if want then
        if after == true then our_pause = true end
    else
        our_pause = false
    end

    if not ok then
        return finish("ERR", "raised", engine_name .. "() raised: " .. tostring(err)
            .. ". The state was read back anyway, because a call can fail AFTER acting -- "
            .. "see after=."
            .. (want and "" or " The claim on our pause was RELEASED; if the game is still "
                                .. "paused, unpause it in game."))
    end
    if after == nil then
        return finish("ERR", "unverified", engine_name .. "() returned, but the state "
            .. "could not be read back (" .. tostring(awhy) .. "). The write is "
            .. "UNVERIFIED -- check it with `x4live pausestate`."
            .. (want and "" or " The claim on our pause was RELEASED."))
    end
    if after ~= want then
        return finish("OK", "disagree", "the engine accepted " .. engine_name .. "() but "
            .. "reads back paused=" .. tostring(after) .. ", not " .. tostring(want)
            .. ". Reported as read, not as intended."
            .. (want and " Ownership was NOT claimed."
                      or (" Ownership is RELEASED and this channel will not retry: another "
                          .. "pause (an open menu, the player) may be holding the game. "
                          .. "Unpause it in game if it should be running.")))
    end
    return finish("OK", "ok", want
        and "the game is PAUSED, read back from the engine. Undo it with `x4live "
            .. "unpause` or by unpausing in game. If the UI has reloaded since, only "
            .. "unpausing in game will undo it."
        or "the game is RUNNING again, read back from the engine.")
end

WRITE_VERBS.pause = function(seq, ...)
    return pause_write(seq, "pause", true, "Pause", select("#", ...))
end

WRITE_VERBS.unpause = function(seq, ...)
    return pause_write(seq, "unpause", false, "Unpause", select("#", ...))
end

--: A READ, and so in `verbs`: the pause state and who owns it, changing neither -- not
--: even a stale ownership flag. It is how a caller whose `pause` reply was lost finds
--: out whether the pause landed: the host writes a command BEFORE it reads the reply
--: and never resends, so a lost reply is not a lost write.
verbs.pausestate = function(seq)
    local paused, why = read_paused()
    if paused == nil then
        reply(seq, "ERR", "the pause state cannot be read: " .. tostring(why))
        return
    end
    reply(seq, "OK", table.concat({ "paused=" .. tostring(paused),
                                    "owner=" .. pause_owner(paused),
                                    "build=" .. BUILD }, " ")
        .. "\t" .. (paused and (our_pause and "paused by THIS channel; `x4live unpause` "
                                           .. "can undo it"
                                          or "paused, but NOT by this channel; only the "
                                             .. "player can undo it")
                            or "running"))
end

-- Dedicated structured transport. It never enters the legacy pause advisory path.
-- Legacy reads remain usable but cannot qualify a session-bound campaign result.
local automation,automation_failure=nil,nil
local AUTOMATION_VERBS={}
local AUTOMATION_READS={ping=true,probe=true,player=true,component=true,equipment=true,
    ["equipment-page"]=true,objects=true,stations=true,ships=true,macro=true,
    ext=true,errors=true,pausestate=true,representative=true}
local function automation_read(verb,args)
    assert(AUTOMATION_READS[verb] and type(verbs[verb])=="function","unknown bound read")
    assert(automation_capture==nil,"nested read refused")
    local captured={seq="automation-read",count=0}
    automation_capture=captured
    local ok,why=pcall(verbs[verb],captured.seq,table_unpack(args))
    automation_capture=nil
    assert(ok,"bound read failed: "..tostring(why))
    assert(captured.count==1,"bound read did not reply exactly once")
    return captured.status,captured.payload
end
-- Only identities emitted by this live helper instance may enter native actions.
-- Native globals can return doubles: reject unsafe conversions instead of guessing.
local function automation_resolve_issued(token,kind)
    assert(type(token)=="string" and issued_ids[token]==true,"identity was not issued by this helper")
    assert(kind=="object" or kind=="ship" or kind=="station" or kind=="sector","unknown identity kind")
    local digits
    local converted
    if kind=="sector" then
        digits=token:match("^ID: ([1-9]%d*)$")
        assert(digits,"issued sector token required")
    else
        digits=token:match("^([1-9]%d*)ULL$")
        assert(digits,"issued object token required")
    end
    assert(#digits<=20 and (#digits<20 or digits<="18446744073709551615"),"identity overflow")
    if kind=="sector" then converted=ConvertIDTo64Bit(ConvertStringToLuaID(token))
    else converted=ConvertStringTo64Bit(token) end
    if type(converted)=="number" then
        assert(converted==converted and converted>0 and converted<9007199254740992
            and converted%1==0,"native identity conversion is not exact")
        converted=ffi.new("uint64_t",converted)
    end
    assert(type(converted)=="cdata" and ffi.istype("uint64_t",converted)
        and tostring(converted)==digits.."ULL","native identity roundtrip mismatch")
    if kind=="object" then
        local valid_id=ConvertStringTo64Bit(tostring(converted))
        assert((type(valid_id)=="cdata" and ffi.istype("uint64_t",valid_id)) or
            (type(valid_id)=="number" and valid_id==valid_id and valid_id>0
                and valid_id<9007199254740992 and valid_id%1==0),"validity identity conversion is not exact")
        assert(wire(valid_id)==tostring(converted) and IsValidComponent(valid_id)==true,
            "issued object is no longer valid")
    else
        assert(C.IsComponentClass(converted,kind)==true,"issued identity class mismatch")
    end
    assert(issued_ids[token]==true,"issued identity retired during resolution")
    return converted
end
local function ensure_automation()
    assert(automation_failure==nil,automation_failure)
    if automation then return automation end
    local bundle=rawget(_G,"X4ToolkitAutomationBundle")
    assert(type(bundle)=="table" and type(bundle.new)=="function","automation bundle not loaded")
    local ok,result=pcall(function()
        local current=bundle.new({ffi=ffi,C=C,read=automation_read,
            read_verbs=AUTOMATION_READS,globals=_G,Helper=Helper,
            resolve_issued=automation_resolve_issued,clock=function() return C.GetCurrentGameTime() end})
        assert(type(current)=="table" and type(current.encode)=="function"
            and type(current.ui_state)=="function" and type(current.dispose)=="function"
            and type(current.session)=="table" and type(current.session.tick)=="function"
            and type(current.router)=="table" and type(current.router.connect)=="function"
            and type(current.router.bound)=="function","malformed automation bundle instance")
        return current
    end)
    if not ok then
        automation_failure="automation bootstrap unavailable: "..tostring(result)
        error(automation_failure)
    end
    automation=result
    return automation
end
AUTOMATION_VERBS.ui_state=function(seq,challenge,extra)
    assert(extra==nil,"ui_state requires one challenge")
    local current=ensure_automation()
    -- The installed bundle supplies its encoder through this bounded bridge.
    reply(seq,"OK",current.encode(current.ui_state(challenge)))
end
AUTOMATION_VERBS.session_connect=function(seq,...)
    local current=ensure_automation()
    assert(type(Helper)=="table" and type(Helper.addDelayedOneTimeCallbackOnUpdate)=="function",
           "session timeout scheduler unavailable")
    local now=getElapsedTime()
    assert(type(now)=="number" and now==now and now>=0 and now<math.huge,"UI clock unavailable")
    -- Register the deadline BEFORE sending. No retry is hidden in this callback.
    Helper.addDelayedOneTimeCallbackOnUpdate(function()
        if automation==current then current.session.tick() end
    end,false,now+10.25)
    current.router.connect({...},function(status,payload) reply(seq,status,payload) end)
end
AUTOMATION_VERBS.bound=function(seq,...)
    local args={...}
    if args[6]=="action_execute" then
        DebugError("X4TOOLKIT_LIVE AUTOMATION WRITE attempt seq="..tostring(seq))
    end
    local current=ensure_automation()
    reply(seq,"OK",current.router.bound(args))
end

on_message = function(msg)
    if type(msg) ~= "string" or msg == "" then return end
    -- The api's reserved data-channel sentinels. Dispatching on one would answer a
    -- question nobody asked, with a sequence number that then desyncs the FIFO.
    --
    -- CORRECTED 2026-08-29: a LUA callback can only ever receive "ERROR". Scanning
    -- the whole packed api yields 9 "ERROR" and 1 "SUCCESS" and nothing else --
    -- TIMEOUT and CANCELLED are synthesised in MD (named_pipes.xml, cues
    -- Access_Timeout and Reload_Listener) and are structurally unreachable from a
    -- lua callback. The other two are kept as a cheap guard, not because they can
    -- arrive. NB the api has no sentinel ESCAPING: a server that legitimately sent
    -- the literal text "ERROR" would be indistinguishable from a pipe failure. Ours
    -- never can -- every real reply begins with the MR tag.
    if msg == "ERROR" or msg == "TIMEOUT" or msg == "CANCELLED" then
        -- NOT noise. Close_Pipe delivers exactly one of these per armed read while
        -- draining the FIFO, so receiving it MEANS our arm has been destroyed --
        -- pipes.lua:462-471, and Poll_For_Reads:560 is the only path that gets here.
        -- Logging and returning (what this did until 2026-08-29) disarms the channel
        -- permanently after the first disconnect, which is exactly what happened.
        armed = false
        local now = sentinel_clock()
        -- THREE causes, told apart by what this arm saw (2026-11-14):
        --   IDLE    no request arrived: no x4live process holds the pipe. Expected,
        --           and the overwhelmingly common case -- backed off, see REARM_IDLE_MAX.
        --   CLOSED  requests were served and then the client hung up: how every x4live
        --           call ends. Expected; re-arm fast, since a client was just here.
        --   FAULT   the arm was destroyed WHILE a verb ran (a reply's write failed and
        --           Close_Pipe drained the reads under us). A real ERROR, unthrottled --
        --           its rate is bounded by client requests, not by the idle cycle.
        -- `t=` is sentinel_clock() (GetCurRealTime: real time, see its comment). debug.txt
        -- stamps GAME time, which stands still while the game is paused, so two throttled
        -- lines 60 s apart can carry one stamp -- exactly the 2026-11-14 "two lines in one
        -- frame". The field lets a reader tell those apart without reasoning about the
        -- throttle. Its origin is UNMEASURED and need not match the debug.txt stamp's;
        -- compare `t=` values with each other, not with the stamp.
        -- schedule_rearm below still reads getElapsedTime: Helper's deadline is compared
        -- against that clock, so it must stay on it.
        local served, delay = requests_on_arm, REARM_DELAY
        requests_on_arm = 0
        local line
        if in_request then
            rearm_delay = REARM_DELAY
            DebugError("X4TOOLKIT_LIVE pipe sentinel: " .. msg .. " -- arm destroyed "
                       .. "MID-REQUEST, re-arming in " .. delay .. "s t=" .. string.format("%.2f", now))
        else
            if served > 0 then
                rearm_delay = REARM_DELAY
                if (now - last_disconnect_log) >= DISCONNECT_LOG_GAP then
                    last_disconnect_log = now
                    line = "X4TOOLKIT_LIVE pipe client disconnected after " .. served
                           .. " request(s), re-arming in " .. delay .. "s"
                end
            else
                delay = rearm_delay
                rearm_delay = math.min(rearm_delay * 2, REARM_IDLE_MAX)
                if rearm_logs < 3 or (now - last_rearm_log) > 60 then
                    rearm_logs = rearm_logs + 1
                    last_rearm_log = now
                    line = "X4TOOLKIT_LIVE pipe idle: no client, re-arming in " .. delay .. "s"
                end
            end
            if line then DebugError(line .. " t=" .. string.format("%.2f", now)) end
        end
        schedule_rearm(delay)
        return
    end
    -- A real frame (well-formed or not) reached us on this arm: a client is here. This
    -- count alone is what makes the arm's eventual teardown CLOSED rather than IDLE, and
    -- CLOSED resets the back-off to fast when this arm ends.
    requests_on_arm = requests_on_arm + 1

    local f = split_tabs(msg)
    if f[1] ~= TAG_CMD then return end
    if f[2] ~= tostring(PROTO) then
        -- Answer anyway, with the sequence we were given, so the Python side gets a
        -- diagnosable frame rather than a timeout it would have to guess about.
        reply(f[3] or "0", "ERR", "proto " .. tostring(f[2]) .. " != " .. PROTO)
        return
    end

    local seq, verb = f[3], f[4]
    if seq == nil or verb == nil then return end

    writing_verb = nil
    local fn, is_write = AUTOMATION_VERBS[verb] or verbs[verb], false
    if WRITE_VERBS[verb] ~= nil then
        if fn ~= nil then
            reply(seq, "ERR", "verb " .. tostring(verb) .. " is in BOTH the read and the "
                  .. "write table -- refusing to guess which was meant; this is a bug in "
                  .. "the mod. Nothing was run.")
            return
        end
        fn, is_write = WRITE_VERBS[verb], true
    end
    if fn == nil then reply(seq, "ERR", "unknown verb: " .. tostring(verb)) return end

    last_replied_seq = nil
    if is_write then
        -- Every ATTEMPT leaves a line in debug.txt, refused or not: a write to the
        -- running game must be reconstructible afterwards from the engine's own log.
        DebugError("X4TOOLKIT_LIVE WRITE " .. verb .. " seq=" .. tostring(seq))
        writing_verb = verb
    end
    -- All remaining fields, not a fixed three. `component` takes a caller-supplied
    -- field LIST, which is the whole point of it being a general inspector; the
    -- older verbs simply ignore the extra arguments.
    -- `in_request` only LABELS a teardown that lands while the verb runs (see the
    -- sentinel branch); it changes nothing about how the request is handled.
    in_request = true
    local ok, err = pcall(fn, seq, table_unpack(f, 5))
    in_request = false
    if not ok then
        -- A verb that throws must still produce a frame: silence here is
        -- indistinguishable from a hung game.
        --
        -- But EXACTLY ONE frame. Until 2026-08-29 a verb that replied and THEN
        -- raised emitted a second frame for the same seq. Python reads one message
        -- per ask(), so the extra frame stayed queued and became the answer to the
        -- NEXT question -- tripping the sequence check (rc 3) one query after the
        -- real fault, with nothing pointing back to the verb that caused it.
        if last_replied_seq == seq then
            DebugError("X4TOOLKIT_LIVE verb " .. verb .. " raised AFTER replying: "
                       .. tostring(err) .. " -- frame already sent, not re-sending")
        else
            local sent, serr = pcall(reply, seq, "ERR",
                                     "verb " .. verb .. " raised: " .. tostring(err))
            if not sent then
                -- The one outcome we can never allow is TOTAL silence, because it
                -- reads as a hung game. If even the error reply failed, say so in
                -- the log -- this branch was previously swallowed, which is the
                -- exact failure the comment above says it exists to prevent.
                DebugError("X4TOOLKIT_LIVE could not reply to seq " .. tostring(seq)
                           .. ": " .. tostring(serr))
            end
        end
    end
    writing_verb = nil
end

-- Read the engine option, never infer it from config defaults or change it.
-- Vanilla analogue: ego_gameoptions/gameoptions.lua's extension security button.
local function ui_safe_mode_at_load()
    if type(GetUISafeModeOption) ~= "function" then return "unknown" end
    local ok, value = pcall(GetUISafeModeOption)
    if not ok or type(value) ~= "boolean" then return "unknown" end
    return value and "true" or "false"
end

Init = function()
    -- FIRST action, before any pipe logic. See the contracts note at the top.
    DebugError("X4TOOLKIT_LIVE loaded: proto=" .. PROTO .. " pipe=" .. PIPE
               .. " build=" .. BUILD .. " ui_safe_mode=" .. ui_safe_mode_at_load())
    -- An install-time refusal is logged here, AFTER the load marker, never before it.
    if pipe_guard then pipe_guard.report() end
    if type(RegisterEvent)=="function" then
        local hooked,hook_error=pcall(RegisterEvent,"X4ToolkitSession.Invalidated",function()
            issued_ids={}
            if automation then
                local old=automation
                automation=nil
                local ok,result=pcall(old.dispose)
                if not ok or result~=true then
                    automation_failure="automation load retirement failed: "..tostring(result)
                end
            end
        end)
        if not hooked then
            automation_failure="automation invalidation hook unavailable: "..tostring(hook_error)
            DebugError("X4TOOLKIT_LIVE "..automation_failure)
        end
    else
        automation_failure="automation invalidation hook unavailable"
    end

    -- ⚠ IDS DO NOT SURVIVE A SAVE LOAD, AND THE ALLOWLIST MUST NOT EITHER.
    --
    -- ★ CORRECTED 2026-08-29, MEASURED IN GAME WITH A CONTROL IN BOTH DIRECTIONS.
    -- This comment used to claim "loading a different save does NOT re-run this Init
    -- ... so the lua state (and our allowlist) persists across the load", and that the
    -- RegisterEvent hook below is therefore what protects us. BOTH HALVES WERE FALSE:
    --
    --   * A save load DOES re-run Init. It emits this function's own load marker and
    --     the elapsed clock RESETS with it (1289.31 -> 143.77), because a save load is
    --     itself a full UI reload -- the same event alt-enter causes.
    --   * The hook below HAS NEVER FIRED. Across a game start and a save load its log
    --     line appears 0 times, while `RegisterEvent absent` and `could not hook game
    --     load` are also 0 -- so it registers cleanly and never runs. It cannot run:
    --     it subscribes from INSIDE Init, and Init is itself driven by that signal, so
    --     it is always registered AFTER the event it waits for. Because the chunk is
    --     rebuilt on every load, the next load replaces this listener before it could
    --     ever fire. Structurally dead, not accidentally dead.
    --
    -- WHAT ACTUALLY PROTECTS US is the chunk re-creation itself: a fresh chunk gets a
    -- fresh empty `issued_ids`. VERIFIED end to end -- an id accepted before a save
    -- load was refused after it, with a control run proving the check could pass.
    --
    -- The hook is KEPT rather than deleted, for two reasons: deleting working-looking
    -- safety code deserves its own decision, and as defence in depth it costs nothing
    -- if the engine ever stops rebuilding the chunk on load. It is now INSTRUMENTED
    -- (`load_hook_fired`, reported by probe) so its deadness stays visible instead of
    -- being re-assumed by the next reader -- which is how it survived this long.
    -- ⚠ Note what misled us: `probe` reports RegisterEvent as PRESENT, and presence was
    -- read as working. A symbol existing says nothing about a handler ever running.
    --
    -- The hazard the clearing exists to prevent is unchanged and still real:
    -- IsValidComponent catches the DESTROYED case, but not the REUSED-HANDLE case:
    -- if the engine hands the same UniverseID to a different object in the new save,
    -- `component <old id>` would return that object's data under the id you asked
    -- about -- a wrong answer wearing the grammar of a right one, which is worse
    -- than a refusal. So the allowlist is emptied on every game start/load.
    --
    -- `Lua_Loader.Send_Priority_Ready` is the MD signal raised by md/lua_loader.xml's
    -- Reload_Listener, whose <check_any> covers BOTH <event_game_loaded/> and
    -- <event_game_started/>. Registering a second listener alongside lua_loader's own
    -- is additive.
    if type(RegisterEvent) == "function" then
        local ok, err = pcall(RegisterEvent, "Lua_Loader.Send_Priority_Ready", function()
            issued_ids = {}
            load_hook_fired = true
            DebugError("X4TOOLKIT_LIVE game start/load: id allowlist cleared "
                       .. "(NOTE: this line had never once appeared as of 2026-08-29 -- "
                       .. "if you are reading it, the hook is no longer dead and the "
                       .. "comment in Init needs revisiting)")
        end)
        if not ok then
            DebugError("X4TOOLKIT_LIVE could not hook game load: " .. tostring(err)
                       .. " -- ids from a previous save may persist; re-run `player`"
                       .. " or `stations` after loading to re-issue them")
        end
    else
        DebugError("X4TOOLKIT_LIVE RegisterEvent absent; the id allowlist will NOT reset "
                   .. "on a save load")
    end

    if armed then
        DebugError("X4TOOLKIT_LIVE Init ran twice; not re-arming (would duplicate replies)")
        return
    end
    if pipes == nil then
        DebugError("X4TOOLKIT_LIVE ABORT: the named pipes api did not load")
        return
    end

    -- continuous_read = true: the callback stays armed and fires on every server
    -- push (pipes.lua:127, and :513-515 declines to pop the FIFO entry), so the
    -- server drives and the game never polls.
    -- FALLBACK, pre-committed: if this never fires because a passive read needs
    -- priming, set continuous to false and re-arm at the END of on_message. That is
    -- pipe_time.lua's proven write-then-read shape and costs only latency.
    arm_read()
    if armed then
        DebugError("X4TOOLKIT_LIVE armed continuous read on " .. PIPE
                   .. " (re-arms itself on disconnect)")
    end
end

--: F124 PIPE UNREF GUARD -- protects THIS helper's own pipe connections only.
--:
--: MEASURED 2026-11-16 (BLIND-SPOTS F124, root cause): the Mod Support APIs pipe DLL
--: (ui_c_library_winpipe_64, upstream winpipe.c) allocates each File userdata as
--: {HANDLE handle; lua_State *L; int callback; char *buf; int bufsz; HANDLE hWrite}
--: and never writes `callback` (+0x10). File:close() hands that garbage to
--: luaL_unref(L, LUA_REGISTRYINDEX, ref), which overwrites a registry slot and poisons
--: the free list. pipes.lua closes the file on every client hang-up, so every x4live
--: call that disconnects was one garbage unref; X4 later read a clobbered slot and
--: crashed. Offline, with the game's own lua51_64.dll and this DLL, stamping the field
--: after open took registry changes from 200/200 closes to 0/200.
--:
--: THE FIX: wrap the module's Connect_Pipe (pipes.lua calls it through the same table,
--: `L.Connect_Pipe`, before every read and write) and, after it returns, write
--: LUA_NOREF (-2) into +0x10 of a NEWLY opened file on one of OUR pipe names.
--: luaL_unref ignores a negative ref, so the close becomes harmless. Other mods' pipes
--: pass through untouched (user decision: the helper protects its own connections).
--:
--: GATED, refusing with no write on any mismatch: the Mod Support APIs version must be
--: the one measured, the userdata must carry winpipe's File metatable (read, write,
--: close and nothing an io file adds), and the payload must look like that struct
--: (+0x00 handle, +0x08 lua_State and +0x18 buffer non-NULL, +0x20 bufsz == 2048). A
--: refusal logs ONE `pipe unref guard INACTIVE: <reason>` line and `probe` reports it.
--:
--: Self-contained between the markers: the offline DLL test loads exactly this block.
--@@ PIPE_UNREF_GUARD_BEGIN
local PIPE_UNREF_MARK = "__x4toolkit_pipe_unref_guard"
local function new_pipe_unref_guard(d)
    --: GetExtensionList renders content.xml version="195" as "1.95" (READ from the
    --: helper's own uidata EXT dump, 2026-11-16). Exact match, never a range.
    local MSA_ID, MSA_VERSION = "ws_2042901274", "1.95"
    local BUFSZ, NOREF = 2048, -2
    local g = {}
    local state, reason, logged, stamped = "armed", "", false, 0
    local gate = nil            -- nil = not evaluated yet; true / false once decided
    local seen = setmetatable({}, {__mode = "k"})
    local target, wrapper = nil, nil

    local function refuse(why, deferred)
        state, reason = "inactive", tostring(why)
        if not deferred and not logged then
            logged = true
            pcall(d.log, "X4TOOLKIT_LIVE pipe unref guard INACTIVE: " .. reason)
        end
        return false
    end

    local function version_gate()
        if gate ~= nil then return gate end
        gate = false
        local ok, list = pcall(d.extensions)
        if not ok then return refuse("extension list unavailable: " .. tostring(list)) end
        if type(list) ~= "table" then return refuse("extension list is " .. type(list)) end
        local hits = {}
        for _, e in ipairs(list) do
            if type(e) == "table" and tostring(e.id) == MSA_ID then hits[#hits + 1] = e end
        end
        if #hits ~= 1 then
            return refuse("Mod Support APIs (" .. MSA_ID .. ") listed " .. #hits .. " times")
        end
        local v = tostring(hits[1].version)
        if v ~= MSA_VERSION then
            return refuse("Mod Support APIs version " .. v .. " is not the measured " .. MSA_VERSION)
        end
        gate = true
        return true
    end

    --: Is this a winpipe File? winpipe.c File_register: luaL_newmetatable with __index =
    --: itself, holding read, write, close and __gc. An io file has read/write/close too,
    --: so any other method (seek, lines, ...) refuses. No debug library needed.
    local FILE_KEYS = {read = true, write = true, close = true, __gc = true,
                       __index = true, __name = true}
    local function not_a_pipe_file(file)
        if type(getmetatable) ~= "function" then return "getmetatable unavailable" end
        local mt = getmetatable(file)
        if type(mt) ~= "table" then return "pipe file metatable is a " .. type(mt) end
        local methods = rawget(mt, "__index")
        if type(methods) ~= "table" then return "pipe file __index is a " .. type(methods) end
        for _, k in ipairs({"read", "write", "close"}) do
            if type(rawget(methods, k)) ~= "function" then return "pipe file has no " .. k end
        end
        for k in pairs(methods) do
            if not FILE_KEYS[k] then return "pipe file has a foreign method " .. tostring(k) end
        end
        return nil
    end

    --: Checks, then writes. Returns nil on success or the refusal reason.
    local function check_and_stamp(file)
        local ffi = d.ffi
        local p = ffi.cast("uint8_t*", ffi.cast("void*", file))
        if p == nil then return "payload pointer is NULL" end
        local ptr = ffi.cast("void**", p)
        if ptr[0] == nil then return "handle at +0x00 is NULL" end
        if ptr[1] == nil then return "lua_State at +0x08 is NULL" end
        if ptr[3] == nil then return "buffer at +0x18 is NULL" end
        local sz = ffi.cast("int32_t*", p + 0x20)[0]
        if sz ~= BUFSZ then return "bufsz at +0x20 is " .. tostring(sz) .. ", not " .. BUFSZ end
        local ref = ffi.cast("int32_t*", p + 0x10)
        ref[0] = NOREF
        if ref[0] ~= NOREF then return "ref at +0x10 did not read back" end
        return nil
    end

    local function stamp(file)
        if state == "inactive" then return false end
        if type(file) ~= "userdata" then return refuse("pipe file is a " .. type(file)) end
        if seen[file] then return true end
        if not version_gate() then return false end
        local ok_kind, foreign = pcall(not_a_pipe_file, file)
        if not ok_kind then return refuse("file type check raised: " .. tostring(foreign)) end
        if foreign ~= nil then return refuse(foreign) end
        if type(d.ffi) ~= "table" or type(d.ffi.cast) ~= "function" then
            return refuse("ffi unavailable")
        end
        local ok, why = pcall(check_and_stamp, file)
        if not ok then return refuse("structural check raised: " .. tostring(why)) end
        if why ~= nil then return refuse(why) end
        seen[file] = true
        stamped = stamped + 1
        state = "active"
        return true
    end

    function g.install(tbl)
        if type(tbl) ~= "table" then return refuse("pipes module is a " .. type(tbl), true) end
        local orig = tbl.Connect_Pipe
        if type(orig) ~= "function" then return refuse("Connect_Pipe absent", true) end
        if type(tbl.pipes) ~= "table" then return refuse("pipes state table absent", true) end
        local function after(name, ...)
            if type(name) == "string" and d.own[name] then
                local ok, file = pcall(function()
                    local entry = tbl.pipes[name]
                    return type(entry) == "table" and entry.file or nil
                end)
                if ok and file ~= nil then pcall(stamp, file) end
            end
            return ...
        end
        -- Errors from the original propagate unchanged: nothing opened, nothing stamped.
        wrapper = function(name, ...) return after(name, orig(name, ...)) end
        target = tbl
        tbl.Connect_Pipe = wrapper
        rawset(tbl, PIPE_UNREF_MARK, g)
        return true
    end

    --: Read-only: a replaced Connect_Pipe is REPORTED, never a refusal. Another mod
    --: wrapping ours still calls our wrapper, so stamping must go on.
    function g.status()
        if state == "inactive" then return "inactive:" .. reason end
        if target ~= nil and target.Connect_Pipe ~= wrapper then
            return state .. ";wrapper-replaced"
        end
        return state
    end
    function g.stamped() return stamped end
    function g.report()
        if state == "inactive" and not logged then
            logged = true
            pcall(d.log, "X4TOOLKIT_LIVE pipe unref guard INACTIVE: " .. reason)
        end
    end
    g.stamp = stamp
    return g
end

--: Idempotent: a table already wrapped returns the guard that wrapped it, so two
--: install points (start menu and world) never stack two wrappers.
local function install_pipe_unref_guard(tbl, d)
    if type(tbl) == "table" then
        local existing = rawget(tbl, PIPE_UNREF_MARK)
        if type(existing) == "table" then return existing end
    end
    local g = new_pipe_unref_guard(d)
    g.install(tbl)
    -- A refused install marks the table too: the second install point then reuses this
    -- guard (one INACTIVE line per table) instead of refusing and logging again.
    if type(tbl) == "table" and rawget(tbl, PIPE_UNREF_MARK) == nil then
        rawset(tbl, PIPE_UNREF_MARK, g)
    end
    return g
end
--@@ PIPE_UNREF_GUARD_END

local function guard_pipes(tbl)
    return install_pipe_unref_guard(tbl, {
        ffi = ffi,
        own = {[PIPE] = true, ["x4live_startmenu"] = true},
        log = DebugError,
        extensions = function()
            if type(GetExtensionList) ~= "function" then error("GetExtensionList absent", 0) end
            return GetExtensionList()
        end,
    })
end

-- UI-only startup uses the supported per-pipe module before the world MD loader.
-- It does not initialize shared MSA polling or create a world session.
local ui_bundle=rawget(_G,"X4ToolkitAutomationBundle")
if type(ui_bundle)=="table" and type(ui_bundle.start_ui)=="function" then
    local ui_ok,ui_result=pcall(function()
        assert(ffi and C,"native UI FFI unavailable")
        -- Native helper.lua declares this exact signature; declare our own dependency
        -- before the first lookup instead of relying on another addon having run.
        ffi.cdef([[ bool IsStartmenu(void); ]])
        if C.IsStartmenu()~=true then return "NOT_START_MENU" end
        local direct=require("extensions.sn_mod_support_apis.ui.named_pipes.Pipes")
        pipe_guard=guard_pipes(direct)
        local result=ui_bundle.start_ui({globals=_G,C=C,Helper=Helper,pipes=direct})
        assert(type(result)=="table" and type(result.status)=="string","malformed UI startup result")
        return result.status
    end)
    DebugError("X4TOOLKIT_UI_START "..(ui_ok and "OBSERVED " or "UNAVAILABLE ")..tostring(ui_result)
        .." build="..tostring(ui_bundle.build))
    -- Init (and its report) may never run in the start menu: log an install-time
    -- refusal here, AFTER this path's marker. report() logs at most once per guard.
    if pipe_guard then pipe_guard.report() end
end

-- The pipes api and Register_OnLoad_Init are both provided by sn_mod_support_apis.
-- Guarded rather than assumed: if that mod is absent or disabled these are nil, and
-- an unguarded require would raise at load time with no explanation of why.
local req_ok, req = pcall(require, "extensions.sn_mod_support_apis.ui.named_pipes.Interface")
if req_ok then
    pipes = req
    -- Before Init arms the first read, so the very first own connection is stamped.
    pipe_guard = guard_pipes(req)
else
    DebugError("X4TOOLKIT_LIVE ABORT: could not require the named pipes api: " .. tostring(req))
end

if type(Register_OnLoad_Init) == "function" then
    Register_OnLoad_Init(Init, "extensions.x4_toolkit_live_query.Live_Query")
else
    DebugError("X4TOOLKIT_LIVE ABORT: Register_OnLoad_Init absent -- is sn_mod_support_apis "
               .. "(ws_2042901274) installed, enabled, and loading BEFORE this mod?")
end

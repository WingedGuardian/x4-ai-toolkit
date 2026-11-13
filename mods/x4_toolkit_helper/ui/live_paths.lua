-- Read-only native save-folder observation. No filesystem or game mutations.
-- Native analogue: gameoptions.lua GetSaveFolderPath declaration and UI display.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C and type(d.current)=="function","native path dependencies")
    local ffi,C,current=d.ffi,d.C,d.current
    ffi.cdef("const char* GetSaveFolderPath(void);")
    local api,disposed={},false
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function stamp()
        assert(not disposed,"native path observer disposed")
        local s=current()
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical public stamp unavailable")
        local expected={provider=true,revision=true,session=true,generation=true,world_token=true}
        local n=0;for k in pairs(s) do assert(expected[k],"unexpected stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string"
            and #s.world_token>0 and #s.world_token<=4096,"incomplete public stamp")
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,
            world_token=s.world_token}
    end
    local function observe()
        local before=stamp()
        local pointer=C.GetSaveFolderPath()
        assert(type(pointer)=="cdata" and ffi.istype("const char *",pointer) and pointer~=ffi.NULL,
            "native save-folder pointer unavailable")
        -- Bounded NUL-terminated read: do not materialize an unbounded ffi.string.
        local bytes,ended={},false
        for index=0,4096 do
            local byte=tonumber(pointer[index])
            if byte==0 then ended=true;break end
            assert(index<4096,"native save-folder exceeds 4096-byte bound")
            if byte<0 then byte=byte+256 end
            assert(byte>=32 and byte~=127,"native save-folder contains control bytes")
            bytes[#bytes+1]=string.char(byte)
        end
        assert(ended and #bytes>0,"native save-folder empty or unterminated")
        local folder=table.concat(bytes)
        local after=stamp()
        for k,v in pairs(before) do assert(after[k]==v,"public identity changed during path observation") end
        return {status="OBSERVED",scope="NATIVE_SAVE_FOLDER_PATH",stamp=before,save_folder=folder}
    end
    function api.read()
        local ok,result=pcall(observe)
        if ok then return result end
        local reason=tostring(result)
        return {status="UNAVAILABLE",scope="NATIVE_SAVE_FOLDER_PATH",reason=reason:sub(1,1024)}
    end
    function api.dispose() disposed=true;return true end
    return api
end

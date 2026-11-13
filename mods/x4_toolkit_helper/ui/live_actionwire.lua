-- Bounded scalar request codec and JSON receipt encoder. No native calls.
-- Returns a factory so offline tests exercise actual Lua without loading a mod.
return function()
    local api = {}
    local function split(s, delimiter)
        local out, first = {}, 1
        while true do
            local at = s:find(delimiter, first, true)
            if not at then out[#out+1] = s:sub(first); break end
            out[#out+1] = s:sub(first, at-1)
            first = at + #delimiter
        end
        return out
    end
    local function token(s)
        return #s >= 1 and #s <= 128 and not s:find("[^A-Za-z0-9_.:-]")
    end
    local function hex(s,n)
        return #s == n and not s:find("[^0-9a-f]")
    end
    function api.decode(packet)
        assert(type(packet) == "string" and #packet <= 1400, "bounded packet required")
        assert(not packet:find("[^ -~]"), "ASCII wire required")
        local f = split(packet, "|")
        assert(#f == 8 and f[1] == "1" and hex(f[2],32) and hex(f[7],64), "invalid envelope")
        for k=3,6 do assert(token(f[k]), "invalid envelope token") end
        local args, count = {}, 0
        if f[8] ~= "" then
            for _, part in ipairs(split(f[8], ",")) do
                local key, typ, val = part:match("^([A-Za-z_][A-Za-z0-9_]*)=([sbn])(.*)$")
                assert(key and #key <= 64 and args[key] == nil, "invalid/duplicate argument")
                count=count+1; assert(count <= 24, "argument count limit")
                if typ == "s" then
                    assert(#val % 2 == 0 and not val:find("[^0-9a-f]"), "invalid string hex")
                    val = val:gsub("..", function(pair) return string.char(tonumber(pair,16)) end)
                    assert(not val:find("[^ -~]"), "printable ASCII string required")
                elseif typ == "b" then
                    assert(val == "1" or val == "0", "invalid boolean")
                    val = val == "1"
                else
                    assert(val:match("^%-?%d+%.?%d*[eE]?[+-]?%d*$"), "invalid number syntax")
                    val = tonumber(val)
                    assert(val and val == val and math.abs(val) <= 9007199254740991, "unsafe number")
                end
                args[key] = val
            end
        end
        return {version=1,id=f[2],session=f[3],provider=f[4],action=f[5],
            binding=f[6],digest=f[7],arguments=args}
    end
    function api.encode(value)
        local nodes, bytes, active = 0, 0, {}
        local function quote(s)
            assert(#s <= 4096, "string limit")
            return '"' .. s:gsub('[%z\1-\31\\"]', function(c)
                return string.format("\\u%04x", string.byte(c))
            end) .. '"'
        end
        local function put(v, depth)
            nodes=nodes+1; assert(nodes <= 512 and depth <= 6, "JSON depth/node limit")
            local typ, text = type(v), nil
            if typ == "nil" then text="null"
            elseif typ == "boolean" then text=v and "true" or "false"
            elseif typ == "string" then text=quote(v)
            elseif typ == "number" then
                assert(v == v and math.abs(v) <= 9007199254740991, "finite safe JSON number required")
                text=string.format("%.17g",v)
            else
                assert(typ == "table" and getmetatable(v) == nil and not active[v], "plain acyclic JSON table required")
                active[v]=true
                local keys, numeric = {}, 0
                for k in pairs(v) do
                    assert(type(k) == "string" or (type(k) == "number" and k >= 1 and k%1 == 0), "invalid JSON key")
                    keys[#keys+1]=k
                    if type(k) == "number" then numeric=numeric+1 end
                end
                local parts={}
                if numeric > 0 then
                    assert(numeric == #keys and #v == numeric, "sparse/mixed table refused")
                    for k=1,numeric do parts[#parts+1]=put(v[k],depth+1) end
                    text="["..table.concat(parts,",").."]"
                else
                    table.sort(keys)
                    for _,k in ipairs(keys) do parts[#parts+1]=quote(k)..":"..put(v[k],depth+1) end
                    text="{"..table.concat(parts,",").."}"
                end
                active[v]=nil
            end
            bytes=bytes+#text; assert(bytes <= 65536, "JSON byte budget")
            return text
        end
        return put(value,0)
    end
    return api
end


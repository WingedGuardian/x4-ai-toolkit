-- Read native orientation and follow-camera offset SETTINGS, never achieved pose.
return function(d)
    assert(type(d)=="table" and d.ffi and d.C and type(d.current)=="function"
        and type(d.now)=="function","camera observation dependencies")
    local ffi,C=d.ffi,d.C
    local function structure(name,fields)
        if not pcall(ffi.typeof,name) then ffi.cdef("typedef struct {"..fields.."} "..name..";") end
        local proof=ffi.typeof("struct {"..fields.."}")
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),"native camera ABI size")
        local item=ffi.new(name)
        for field in fields:gmatch("float%s+(%w+)%s*;") do
            assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field),"native camera ABI field")
            item[field]=1.25;assert(tonumber(item[field])==1.25,"native camera ABI float")
            item[field]=16777217;assert(tonumber(item[field])==16777216,"native camera ABI float32")
        end
    end
    structure("Rotation","float yaw; float pitch; float roll;")
    structure("Coord3D","float x; float y; float z;")
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.istype("uint64_t",ffi.new("UniverseID")),"native camera UniverseID ABI")
    ffi.cdef([[Rotation GetCameraRotation(void);
        Coord3D GetFollowCameraBasePos(void);
        UniverseID GetExternalViewRefObject(void);
        bool IsExternalViewActive(void);]])
    local api,disposed={},false
    local function finite(v)
        return type(v)=="number" and v==v and v~=math.huge and v~=-math.huge
    end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function stamp()
        assert(not disposed,"camera observer disposed")
        local s=d.current()
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical public stamp unavailable")
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        local n=0;for k in pairs(s) do assert(keys[k],"unexpected stamp field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=="string"
            and #s.world_token>0 and #s.world_token<=4096,"incomplete public stamp")
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,world_token=s.world_token}
    end
    local function reference()
        local v=C.GetExternalViewRefObject()
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"native external reference unavailable")
        local text=tostring(v):match("^(%d+)ULL$")
        assert(text,"native external reference not exact uint64")
        return text
    end
    local function active()
        local v=C.IsExternalViewActive()
        assert(type(v)=="boolean","native external-view mode unavailable")
        return v
    end
    local function vector(name,value,fields)
        assert(type(value)=="cdata" and ffi.istype(name,value),"native camera structure unavailable")
        local out={}
        for _,field in ipairs(fields) do
            local v=tonumber(value[field]);assert(finite(v),"nonfinite native camera value");out[field]=v
        end
        return out
    end
    local function observe()
        local before=stamp();local started=d.now()
        assert(finite(started) and started>=0,"camera start clock unavailable")
        local mode,ref=active(),reference()
        local rotation=vector("Rotation",C.GetCameraRotation(),{"yaw","pitch","roll"})
        local offset=vector("Coord3D",C.GetFollowCameraBasePos(),{"x","y","z"})
        assert(reference()==ref and active()==mode,"external camera reference or mode changed")
        local finished=d.now();assert(finite(finished) and finished>=started,"camera clock regressed")
        local after=stamp()
        for k,v in pairs(before) do assert(after[k]==v,"public identity changed during camera observation") end
        return {status="OBSERVED",scope="NATIVE_CAMERA_ORIENTATION_AND_OFFSET_SETTING",stamp=before,
            interval={started=started,finished=finished,clock="INJECTED_UI_CLOCK"},
            rotation_native=rotation,rotation_units="UNKNOWN",rotation_reference_frame="UNKNOWN",
            follow_camera_base_offset_setting=offset,external_view_active=mode,
            external_reference_handle=ref,external_reference_nonzero=(ref~="0"),
            nested_handle_issued=false,achieved_pose_qualified=false,target_distance_qualified=false,
            engine_atomicity_qualified=false}
    end
    function api.read()
        local ok,result=pcall(observe)
        if ok then return result end
        return {status="UNAVAILABLE",scope="NATIVE_CAMERA_ORIENTATION_AND_OFFSET_SETTING",reason=tostring(result):sub(1,1024)}
    end
    function api.dispose() disposed=true;return true end
    return api
end

-- Optional Extra Data API interface adapter. No pointer code or bundled dependency.
-- Only explicit reads invoke GetMacroDataEx; its first read may initialize the
-- external library and write its UI userdata/crash sentinel. Never call Available.
return function(d)
    assert(type(d)=="table","geometry dependencies")
    for _,k in ipairs({"current","game_build","dependency","external","encode"}) do
        assert(type(d[k])=="function","trusted geometry dependency callback required")
    end
    local function plain(v) return type(v)=="table" and getmetatable(v)==nil end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function text(v,maximum,empty)
        assert(type(v)=="string" and #v<=maximum and (empty or #v>0)
            and not v:find("[%z\1-\31\127]"),"bounded text required")
        return v
    end
    local function fields(v,allowed)
        assert(plain(v),"plain table required")
        for k in pairs(v) do assert(allowed[k],"unexpected field") end
    end
    local identity_fields={extension_id=true,release_version=true,interface_version=true,
        lua_sha256=true,archive_sha256=true,installed_receipt_sha256=true,game_build=true}
    local function identity(v)
        fields(v,identity_fields)
        assert(v.extension_id=="ws_3825599643" and v.interface_version==100
            and type(v.release_version)=="number" and v.release_version%1==0 and v.release_version>=103,
            "qualified Extra Data interface required")
        local out={}
        for k in pairs(identity_fields) do
            if k:find("sha256",1,true) then assert(hex(v[k],64),"dependency hash required") end
            assert(v[k]~=nil,"dependency identity incomplete");out[k]=v[k]
        end
        text(out.game_build,128,false)
        return out
    end
    local expected=identity(d.expected)
    local disposed=false
    local function equal(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function stamp()
        assert(not disposed,"geometry observer disposed")
        local s=d.current()
        fields(s,{provider=true,revision=true,session=true,generation=true,world_token=true})
        assert(s.provider=="x4-live-session-v1" and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32),"public identity unavailable")
        text(s.world_token,4096,false)
        return {provider=s.provider,revision=s.revision,session=s.session,generation=s.generation,
            world_token=s.world_token}
    end
    local function context()
        local s=stamp()
        assert(d.game_build()==expected.game_build,"game build differs from qualified dependency")
        assert(equal(identity(d.dependency()),expected),"installed dependency identity changed")
        local ext=d.external()
        assert(plain(ext) and ext.version==expected.interface_version
            and type(ext.GetMacroDataEx)=="function","Extra Data interface unavailable")
        assert(equal(s,stamp()),"public identity changed during dependency checks")
        return s,ext,ext.GetMacroDataEx
    end
    local function finish(s,ext,getter)
        local after,current_ext,current_getter=context()
        assert(equal(s,after) and ext==current_ext and getter==current_getter,
            "identity or external getter changed during observation")
    end
    local function finite(v) return type(v)=="number" and v==v and v>-math.huge and v<math.huge end
    local function dense(v,maximum)
        assert(plain(v),"dense array required")
        local n=0
        for k in pairs(v) do
            n=n+1;assert(n<=maximum and type(k)=="number" and k%1==0 and k>=1
                and k<=maximum,"array bound exceeded")
        end
        for i=1,n do assert(v[i]~=nil,"array hole") end
        return n
    end
    local function vec(v,nonnegative)
        assert(dense(v,3)==3,"three coordinates required")
        for i=1,3 do assert(finite(v[i]) and (not nonnegative or v[i]>=0),"invalid coordinate") end
        return {x=v[1],y=v[2],z=v[3]}
    end
    local function box(v)
        if v==nil then return {status="UNKNOWN",reason="EXTERNAL_NIL_NOT_ABSENCE"} end
        fields(v,{max=true,centre=true,radius=true})
        assert(finite(v.radius) and v.radius>=0,"invalid radius")
        return {status="OBSERVED",max=vec(v.max,true),centre=vec(v.centre,false),radius=v.radius}
    end
    local function row(v)
        fields(v,{name=true,tags=true,position=true,direction=true,rows=true,macro=true,count=true,
            sizetag=true,size=true,centre=true})
        local out={name=text(v.name,256,false),tags={},position=vec(v.position,false),
            direction=vec(v.direction,false),rows={}}
        local n=dense(v.tags,32)
        for i=1,n do out.tags[i]=text(v.tags[i],128,false) end
        assert(dense(v.rows,3)==3,"three matrix rows required")
        for i=1,3 do out.rows[i]=vec(v.rows[i],false) end
        assert(equal(out.direction,out.rows[3]),"direction/matrix disagreement")
        assert(finite(v.count) and v.count>=0 and v.count%1==0 and v.count<=4096,
            "invalid connection usage count")
        out.count=v.count
        out.child_macro_status=v.macro==nil and "NOT_REPORTED" or "REPORTED"
        if v.macro~=nil then out.macro=text(v.macro,256,false) end
        if v.sizetag~=nil or v.size~=nil or v.centre~=nil then
            out.sizetag=text(v.sizetag,128,false);out.size=vec(v.size,true);out.centre=vec(v.centre,false)
        end
        return out
    end
    local function macro(v)
        text(v,256,false)
        assert(not v:find("[^%w_.-]"),"macro identifier required")
        return v
    end
    local function header(s,name,kind)
        return {status="OBSERVED",scope="COMPILED_MACRO_DEFINITION_NOT_FITTED_INSTANCE",
            getter=kind,macro=name,stamp=s,dependency=identity(expected),
            dependency_binding="TRUSTED_INSTALLED_RECEIPT_CALLBACK",
            evidence_tier="EXTERNAL_API_OBSERVATION_UNCALIBRATED",numeric_accuracy_qualified=false,
            geometry_frame="MACRO_LOCAL",achieved_pose_verified=false}
    end
    local function encoded(v)
        local out=d.encode(v)
        assert(type(out)=="string","encoded observation required")
        return #out
    end
    local function boxes(name)
        name=macro(name)
        local s,ext,getter=context()
        local raw_a,raw_b=getter(name,"boundingbox","fullboundingbox")
        local a,b=box(raw_a),box(raw_b)
        finish(s,ext,getter)
        local raw_c,raw_e=getter(name,"boundingbox","fullboundingbox")
        assert(equal(a,box(raw_c)) and equal(b,box(raw_e)),"macro boxes changed during repeat read")
        finish(s,ext,getter)
        local out=header(s,name,"boundingbox+fullboundingbox")
        out.boundingbox,out.fullboundingbox=a,b
        out.status=(a.status=="OBSERVED" and b.status=="OBSERVED") and "OBSERVED"
            or ((a.status=="UNKNOWN" and b.status=="UNKNOWN") and "UNKNOWN" or "PARTIAL")
        assert(encoded(out)<=3000,"observation exceeds wire payload budget")
        finish(s,ext,getter)
        return out
    end
    local function connections(name,offset)
        name=macro(name);if offset==nil then offset=0 end
        assert(type(offset)=="number" and offset>=0 and offset%1==0 and offset<=256,"bounded offset required")
        local s,ext,getter=context()
        local first=getter(name,"connections")
        if first==nil then
            finish(s,ext,getter)
            local second=getter(name,"connections")
            assert(second==nil,"connection availability changed")
            finish(s,ext,getter)
            local out=header(s,name,"connections")
            out.status="UNAVAILABLE";out.reason="EXTERNAL_NIL_NOT_EMPTY"
            assert(encoded(out)<=3000,"unavailable observation exceeds wire budget")
            finish(s,ext,getter)
            return out
        end
        local n=dense(first,256)
        assert(offset<=n,"offset outside observed denominator")
        local selected={}
        -- Normalize before repeating: external implementations may reuse mutable tables.
        local normalized={}
        local charge=0
        for i=1,n do
            local r=row(first[i]);charge=charge+encoded(r)
            assert(charge<=262144,"connection census normalization byte bound")
            normalized[i]=r
        end
        finish(s,ext,getter)
        local second=getter(name,"connections")
        assert(dense(second,256)==n,"connection denominator changed")
        for i=1,n do assert(equal(normalized[i],row(second[i])),"connection row changed") end
        for i=offset+1,math.min(n,offset+8) do selected[#selected+1]=normalized[i] end
        finish(s,ext,getter)
        local out=header(s,name,"connections")
        out.total=n;out.offset=offset;out.rows=selected;out.returned=#selected
        out.omitted=n-#selected;out.next_offset=offset+#selected
        out.page_complete=out.next_offset==n;out.full_census_in_this_reply=offset==0 and #selected==n
        out.cross_page_snapshot=false
        while encoded(out)>3000 and #selected>0 do
            selected[#selected]=nil;out.returned=#selected;out.omitted=n-#selected
            out.next_offset=offset+#selected;out.page_complete=out.next_offset==n
            out.full_census_in_this_reply=offset==0 and #selected==n
        end
        assert(encoded(out)<=3000 and (offset==n or #selected>0),"single row exceeds wire budget")
        -- Each call is a fresh repeat-stable observation, not an immutable cross-page capture.
        assert(encoded(out)<=3000,"final observation exceeds wire budget")
        finish(s,ext,getter)
        return out
    end
    local function safe(fn,...)
        local ok,result=pcall(fn,...)
        if ok then return result end
        return {status="UNAVAILABLE",scope="COMPILED_MACRO_DEFINITION_NOT_FITTED_INSTANCE",
            reason=tostring(result):sub(1,512)}
    end
    local api={}
    function api.boxes(name) return safe(boxes,name) end
    function api.connections(name,offset) return safe(connections,name,offset) end
    function api.availability()
        return {status=disposed and "DISPOSED" or "UNPROBED",
            external_initialization_may_write_ui_userdata=true}
    end
    function api.dispose() disposed=true;return true end
    return api
end

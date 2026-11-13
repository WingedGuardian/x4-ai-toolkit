-- Read a currently bound native HUD target message; not a cutscene pose.
return function(d)
    assert(type(d)=='table' and d.ffi and d.C and type(d.globals)=='table','projection dependencies')
    for _,k in ipairs({'resolve_issued','is_valid','current','now','encode'})do assert(type(d[k])=='function','missing '..k)end
    local ffi,C,g=d.ffi,d.C,d.globals
    if not pcall(ffi.typeof,'UniverseID')then ffi.cdef('typedef uint64_t UniverseID;')end
    assert(ffi.istype('uint64_t',ffi.new('UniverseID')),'native unsigned identity')
    local layouts={
        SofttargetDetails2={body='UniverseID softtargetID;const char* softtargetConnectionName;uint32_t messageID;',
            id={'softtargetID'},text={'softtargetConnectionName'},uint={'messageID'}},
        MessageDetails3={body='uint64_t poiID;UniverseID componentID;const char* messageType;const char* connectionName;bool isAssociative;bool isMissionTarget;bool isPriorityMissionTarget;bool showIndicator;bool hasAdditionalOffset;',
            id={'poiID','componentID'},text={'messageType','connectionName'},
            bool={'isAssociative','isMissionTarget','isPriorityMissionTarget','showIndicator','hasAdditionalOffset'}}}
    for name,l in pairs(layouts)do
        if not pcall(ffi.typeof,name)then ffi.cdef('typedef struct {'..l.body..'} '..name..';')end
        local proof=ffi.typeof('struct {'..l.body..'}')
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),'projection ABI size')
        local actual,expected=ffi.new(name),ffi.new(proof)
        for kind,fields in pairs(l)do if kind~='body'then for _,field in ipairs(fields)do
            assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field),'projection ABI offset')
            if kind=='id'then assert(ffi.istype('uint64_t',actual[field]),'projection ABI uint64')
            elseif kind=='text'then assert(ffi.istype('const char*',actual[field]),'projection ABI string')
            elseif kind=='bool'then assert(type(actual[field])=='boolean','projection ABI bool');actual[field]=true;expected[field]=true
            else assert(type(actual[field])=='number','projection ABI 32-bit scalar');actual[field]=4294967295;expected[field]=4294967295;assert(actual[field]==4294967295,'projection ABI uint32')end
        end end end
        assert(ffi.string(ffi.cast('char*',actual),ffi.sizeof(actual))==ffi.string(ffi.cast('char*',expected),ffi.sizeof(expected)),'projection ABI representation')
    end
    ffi.cdef[[SofttargetDetails2 GetSofttarget2(void);
        MessageDetails3 GetMessageDetails3(const uint32_t messageid);
        UniverseID GetExternalViewRefObject(void);
        bool IsExternalViewActive(void);]]
    local api,disposed,last={},false,nil
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<=9007199254740991 end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    local function stamp()
        assert(not disposed,'projection disposed')
        local s=d.current();local out,n={},0
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        assert(type(s)=='table' and getmetatable(s)==nil,'public stamp unavailable')
        for k,v in pairs(s)do assert(keys[k],'unexpected stamp');out[k]=v;n=n+1 end
        assert(n==5 and out.provider=='x4-live-session-v1' and hex(out.revision,64) and hex(out.session,32)
            and hex(out.generation,32) and type(out.world_token)=='string' and #out.world_token>0 and #out.world_token<=512,'public stamp malformed')
        return out
    end
    local function clock()
        local t=d.now();assert(finite(t) and t>=0 and (not last or t>=last),'UI clock unavailable/regressed');last=t;return t
    end
    local function ident(v,zero)
        assert(type(v)=='cdata' and ffi.istype('uint64_t',v),'native exact ID')
        local s=tostring(v):match('^(%d+)ULL$');assert(s and (zero or s~='0'),'native ID absent');return s
    end
    local function text(p)
        assert(p~=nil and p~=ffi.NULL,'native string unavailable')
        local n=0;while n<=128 and p[n]~=0 do n=n+1 end
        assert(n<=128,'native string bound');local s=ffi.string(p,n)
        assert(not s:find('[%z\1-\31]'),'native control text');return s
    end
    local function resolve(token)
        assert(type(token)=='string' and #token>0 and #token<=128 and not token:find('[%c]'),'issued token')
        local v=d.resolve_issued(token,'object');local id=ident(v)
        assert(d.is_valid(v)==true,'component unavailable');return v,id
    end
    local function view()
        local mode=C.IsExternalViewActive();assert(type(mode)=='boolean','external view unavailable')
        return {external=mode,reference=ident(C.GetExternalViewRefObject(),true)}
    end
    local function binding(id)
        local s=C.GetSofttarget2();assert(type(s)=='cdata' and ffi.istype('SofttargetDetails2',s),'softtarget unreadable')
        assert(ident(s.softtargetID)==id,'issued component is not observed softtarget')
        local message=tonumber(s.messageID);assert(message>0 and message<=4294967295,'message absent')
        local connection=text(s.softtargetConnectionName)
        local m=C.GetMessageDetails3(message)
        assert(type(m)=='cdata' and ffi.istype('MessageDetails3',m),'message unreadable')
        assert(ident(m.componentID)==id and text(m.connectionName)==connection,'message binding differs')
        return {message=message,component=id,connection=connection,message_type=text(m.messageType),
            poi=ident(m.poiID,true),associative=m.isAssociative,mission=m.isMissionTarget,
            priority=m.isPriorityMissionTarget,indicator=m.showIndicator,additional_offset=m.hasAdditionalOffset}
    end
    local function same(a,b)
        for k,v in pairs(a)do assert(b[k]==v,'sample binding changed')end
        for k in pairs(b)do assert(a[k]~=nil,'sample binding changed')end
    end
    local function observe(token)
        assert(type(g.GetMessageScreenPosition)=='function','GetMessageScreenPosition unavailable in this Lua environment')
        local before=stamp();local started=clock();local object,id=resolve(token)
        local v=view();local b=binding(id)
        local x,y,on_screen,distance=g.GetMessageScreenPosition(b.message)
        assert(finite(x) and finite(y) and type(on_screen)=='boolean' and finite(distance) and distance>=0,'projection unavailable')
        same(b,binding(id));same(v,view())
        local after_object,after_id=resolve(token);assert(after_object==object and after_id==id,'issued identity changed')
        local finished=clock();same(before,stamp())
        local result={status='OBSERVED',scope='CURRENT_HUD_TARGET_MESSAGE_PROJECTION',stamp=before,
            interval={started=started,finished=finished,clock='UI_ELAPSED_TIME'},
            binding=b,view=v,projection={x=x,y=y,on_screen=on_screen,camera_distance_native=distance},
            distance_semantics='TARGET_MESSAGE_GEOMETRY_NOT_PROVEN_OBJECT_CENTER',
            coordinate_units='NATIVE_UI_UNKNOWN',distance_units='NATIVE_DISTANCE_NOT_INDEPENDENTLY_CALIBRATED',
            view_domain='HUD_TARGETSYSTEM_CUTSCENE_BINDING_UNQUALIFIED',atomic=false,
            nested_handle_issued=false,achieved_pose_verified=false,cutscene_view_verified=false,
            rendered_pixels_verified=false,runtime_verified=false}
        assert(#d.encode({payload=d.encode(result),reserved=string.rep('x',900)})<=4000,'projection wire bound')
        return result
    end
    function api.read(token)
        local ok,value=pcall(observe,token)
        if ok then return value end
        return {status='UNAVAILABLE',scope='CURRENT_HUD_TARGET_MESSAGE_PROJECTION',reason=tostring(value):sub(1,512),
            achieved_pose_verified=false,cutscene_view_verified=false,runtime_verified=false}
    end
    function api.dispose()disposed=true;return true end
    return api
end

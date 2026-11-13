-- Installed equipment modifications and their native roll values, not fitted macros.
-- Point reads only; no reroll/install/remove calls. See qualification doc.
return function(d)
    local ffi,C,g=d.ffi,d.C,d.globals
    assert(ffi and C and type(g)=="table","native dependencies required")
    for _,k in ipairs({"resolve_issued","current","now","encode"}) do assert(type(d[k])=="function","missing "..k) end
    assert(type(g.IsValidComponent)=="function","native validity required")
    if not pcall(ffi.typeof,"UniverseID") then ffi.cdef("typedef uint64_t UniverseID;") end
    assert(ffi.istype("uint64_t",ffi.new("UniverseID")),"unsigned native identity required")
    local layouts={UIShipMod2={fields="const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float MassFactor,DragFactor,MaxHullFactor,RadarRangeFactor;uint32_t AddedUnitCapacity,AddedMissileCapacity,AddedCountermeasureCapacity,AddedDeployableCapacity;float RadarCloakFactor,RegionDamageProtection,HideCargoChance;",floats={"MassFactor","DragFactor","MaxHullFactor","RadarRangeFactor","RadarCloakFactor","RegionDamageProtection","HideCargoChance"},uints={"Quality","AddedUnitCapacity","AddedMissileCapacity","AddedCountermeasureCapacity","AddedDeployableCapacity"}},
UIEngineMod2={fields="const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float ForwardThrustFactor,StrafeAccFactor,StrafeThrustFactor,RotationThrustFactor,BoostAccFactor,BoostThrustFactor,BoostDurationFactor,BoostAttackTimeFactor,BoostReleaseTimeFactor,BoostChargeTimeFactor,BoostRechargeTimeFactor,TravelThrustFactor,TravelStartThrustFactor,TravelAttackTimeFactor,TravelReleaseTimeFactor,TravelChargeTimeFactor;",floats={"ForwardThrustFactor","StrafeAccFactor","StrafeThrustFactor","RotationThrustFactor","BoostAccFactor","BoostThrustFactor","BoostDurationFactor","BoostAttackTimeFactor","BoostReleaseTimeFactor","BoostChargeTimeFactor","BoostRechargeTimeFactor","TravelThrustFactor","TravelStartThrustFactor","TravelAttackTimeFactor","TravelReleaseTimeFactor","TravelChargeTimeFactor"},uints={"Quality"}},
UIShieldMod={fields="const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float CapacityFactor,RechargeDelayFactor,RechargeRateFactor;",floats={"CapacityFactor","RechargeDelayFactor","RechargeRateFactor"},uints={"Quality"}},
UIWeaponMod={fields="const char *Name,*RawName,*Ware;uint32_t Quality;const char *PropertyType;float DamageFactor,CoolingFactor,ReloadFactor,SpeedFactor,LifeTimeFactor,MiningFactor,StickTimeFactor,ChargeTimeFactor,BeamLengthFactor;uint32_t AddedAmount;float RotationSpeedFactor,SurfaceElementFactor;",floats={"DamageFactor","CoolingFactor","ReloadFactor","SpeedFactor","LifeTimeFactor","MiningFactor","StickTimeFactor","ChargeTimeFactor","BeamLengthFactor","RotationSpeedFactor","SurfaceElementFactor"},uints={"Quality","AddedAmount"}}}
    for name,layout in pairs(layouts) do
        if not pcall(ffi.typeof,name) then ffi.cdef("typedef struct {"..layout.fields.."} "..name..";") end
        local proof=ffi.typeof("struct {"..layout.fields.."}")
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),"mod ABI size")
        local actual,expected=ffi.new(name),ffi.new(proof)
        for _,field in ipairs({"Name","RawName","Ware","PropertyType"}) do
            assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field) and ffi.istype("const char *",actual[field]),"mod ABI text")
        end
        for _,field in ipairs(layout.uints) do
            assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field),"mod ABI uint offset")
            actual[field]=4294967295;expected[field]=4294967295;assert(actual[field]==4294967295,"mod ABI unsigned")
        end
        for _,field in ipairs(layout.floats) do
            assert(ffi.offsetof(name,field)==ffi.offsetof(proof,field),"mod ABI float offset")
            actual[field]=0.375;expected[field]=0.375
        end
        assert(ffi.string(ffi.cast("char*",actual),ffi.sizeof(actual))==ffi.string(ffi.cast("char*",expected),ffi.sizeof(expected)),"mod ABI representation")
    end
    local sf="UniverseID context;const char *group;UniverseID component;"
    if not pcall(ffi.typeof,"ShieldGroup") then ffi.cdef("typedef struct {"..sf.."} ShieldGroup;") end
    local proof=ffi.typeof("struct {"..sf.."}");local probe=ffi.new("ShieldGroup")
    assert(ffi.sizeof("ShieldGroup")==ffi.sizeof(proof) and ffi.alignof("ShieldGroup")==ffi.alignof(proof),"group ABI size")
    for _,field in ipairs({"context","group","component"}) do
        assert(ffi.offsetof("ShieldGroup",field)==ffi.offsetof(proof,field),"group ABI offset")
        assert(ffi.istype(field=="group" and "const char *" or "uint64_t",probe[field]),"group ABI type")
    end
    ffi.cdef[[
        bool GetInstalledShipMod2(UniverseID shipid,UIShipMod2* mod);
        bool GetInstalledEngineMod2(UniverseID objectid,UIEngineMod2* mod);
        bool GetInstalledShieldMod(UniverseID defensibleid,UniverseID contextid,const char* group,UIShieldMod* mod);
        bool GetInstalledWeaponMod(UniverseID weaponid,UIWeaponMod* mod);
        bool GetInstalledGroupedWeaponMod(UniverseID defensibleid,UniverseID contextid,const char* group,UIWeaponMod* mod);
        bool GetShieldGroup(ShieldGroup* result,UniverseID defensibleid,UniverseID destructibleid);
        UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);
        bool IsComponentClass(UniverseID componentid,const char* classname);
    ]]
    local api,cache,seen,attempts,lastclock={},nil,{},0,nil
    local MAX_READS,capacity_retired=4096,false
    local function finite(v) return type(v)=="number" and v==v and math.abs(v)<=9007199254740991 end
    local function clock()
        local t=d.now();assert(finite(t) and t>=0 and (not lastclock or t>=lastclock),"clock unavailable/backward")
        lastclock=t;return t
    end
    local function hex(v,n) return type(v)=="string" and #v==n and not v:find("[^0-9a-f]") end
    local function copy(t)
        if type(t)~="table" then return t end
        local o={};for k,v in pairs(t) do o[k]=copy(v) end;return o
    end
    local function equal(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end;return true
    end
    local function stamp()
        local s=d.current();local n=0
        local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
        assert(type(s)=="table" and getmetatable(s)==nil,"canonical session unavailable")
        for k in pairs(s) do assert(keys[k],"unexpected canonical field");n=n+1 end
        assert(n==5 and s.provider=="x4-live-session-v1" and hex(s.revision,64) and hex(s.session,32) and
            hex(s.generation,32) and type(s.world_token)=="string" and #s.world_token>0 and #s.world_token<=4096,"canonical session malformed")
        return copy(s)
    end
    local function ident(v,zero)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"exact native identity required")
        local s=tostring(v):match("^(%d+)ULL$")
        assert(s and (zero or s~="0"),"native identity absent");return s
    end
    local function string_field(v)
        assert(v~=nil and v~=ffi.NULL,"native string unreadable")
        local n=0;while n<=512 and v[n]~=0 do n=n+1 end;assert(n<=512,"native text unterminated/over bound");local s=ffi.string(v,n)
        assert(#s<=512 and not s:find("[%z\1-\31]"),"native text exceeds bound")
        return s -- Empty native macro/faction is retained, never replaced with a guessed label.
    end
    local function count(v)
        assert(finite(v) and v>=0 and v%1==0 and v<=8192,"native count unavailable/over bound")
        return v
    end
    local function encode(v,limit)
        local s=d.encode(v);assert(type(s)=="string" and #s<=limit,"encoded byte bound");return #s
    end
    local function safe(fn)
        local ok,v=pcall(fn)
        return ok and v or {status="REFUSED",complete=false,reason=tostring(v):sub(1,512)}
    end

    local kinds={ship="UIShipMod2",engine="UIEngineMod2",shield="UIShieldMod",weapon="UIWeaponMod",turret="UIWeaponMod",grouped_turret="UIWeaponMod"}
    local function issued(token)
        assert(type(token)=="string" and #token>0 and #token<=128,"issued ID required")
        local obj=d.resolve_issued(token,"object");local id=ident(obj)
        assert(g.IsValidComponent(obj)==true,"issued component invalid");return obj,id
    end
    function api.read(ship_token,kind,component_token)
        return safe(function()
            assert(attempts<MAX_READS,"read budget exhausted");attempts=attempts+1
            local layoutname=kinds[kind];assert(layoutname,"unsupported mod target")
            local before=stamp();local started=clock();local ship,ship_id=issued(ship_token)
            assert(C.IsComponentClass(ship,"ship")==true,"ship required")
            local component,component_id
            if kind=="ship" or kind=="engine" then assert(component_token==nil,"unexpected component")
            else
                component,component_id=issued(component_token)
                assert(C.GetContextByClass(component,"container",false)==ship,"component not in exact immediate ship container")
                if kind~="shield" then assert(C.IsComponentClass(component,"weapon")==true,"weapon component required") end
            end
            local function target()
                local out={ship_id=ship_id,kind=kind,component_id=component_id or ship_id}
                local group
                if kind=="shield" or kind=="grouped_turret" then
                    group=ffi.new("ShieldGroup")
                    assert(C.GetShieldGroup(group,ship,component)==true,"native group unresolved")
                    out.context_id=ident(group.context,true);out.group=string_field(group.group)
                    out.group_component_id=ident(group.component,true)
                    assert(#out.group>0,"native group identifier empty")
                end
                return out,group
            end
            local function observe()
                local out,group=target();local buf=ffi.new(layoutname);local present
                if kind=="ship" then present=C.GetInstalledShipMod2(ship,buf)
                elseif kind=="engine" then present=C.GetInstalledEngineMod2(ship,buf)
                elseif kind=="shield" then present=C.GetInstalledShieldMod(ship,group.context,group.group,buf)
                elseif kind=="grouped_turret" then present=C.GetInstalledGroupedWeaponMod(ship,group.context,group.group,buf)
                else present=C.GetInstalledWeaponMod(component,buf) end
                assert(type(present)=="boolean","mod presence unavailable")
                out.installed=present;out.rolls={}
                if present then
                    for _,field in ipairs({"Name","RawName","Ware","PropertyType"}) do out.rolls[field]=string_field(buf[field]) end
                    for _,field in ipairs(layouts[layoutname].uints) do out.rolls[field]=tonumber(buf[field]) end
                    for _,field in ipairs(layouts[layoutname].floats) do
                        local v=tonumber(buf[field]);assert(finite(v),"nonfinite native mod roll");out.rolls[field]=v
                    end
                end
                return out
            end
            local a=observe();local b=observe();assert(equal(a,b),"installed mod/group changed between samples")
            assert(issued(ship_token)==ship and equal(stamp(),before),"ship/session changed")
            if component then assert(issued(component_token)==component and C.GetContextByClass(component,"container",false)==ship,"component changed") end
            assert(equal(stamp(),before),"session changed during final target recheck")
            local out={status="OBSERVED",complete=true,scope="EXACT_NATIVE_MOD_TARGET_ONLY",atomic=false,
                stamp=before,start_time=started,end_time=clock(),samples=2,target=a,
                fitting_census=false,roll_unit="NATIVE_FIELDS_NO_EFFECTIVE_STAT_INFERENCE"}
            encode(out,3000);return out
        end)
    end
    function api.capacity()
        return {available=not capacity_retired,budgets={reads={used=capacity_retired and 'UNKNOWN' or attempts,limit=MAX_READS}},
            cached_context='NO_RETAINED_SAMPLE'}
    end
    function api.dispose() attempts=MAX_READS;capacity_retired=true;return true end
    return api
end

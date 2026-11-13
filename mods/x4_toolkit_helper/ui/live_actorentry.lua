-- Native room NPC observation and explicitly selected interaction primitives.
-- Transporter813-844; Map5560-5567; InteractMenu116-127/2646/6656-6788;
-- targetsystem1441/3660/4357. No conversation section or economic operation.
return function(d)
    assert(type(d)=='table' and d.ffi and d.C and type(d.globals)=='table','actor entry dependencies')
    for _,k in ipairs({'current','now','encode','component_id','actor_info'})do assert(type(d[k])=='function','missing '..k)end
    local ffi,C,g=d.ffi,d.C,d.globals
    if not pcall(ffi.typeof,'UniverseID')then ffi.cdef('typedef uint64_t UniverseID;')end
    assert(ffi.istype('uint64_t',ffi.new('UniverseID')),'unsigned native identity')
    local layouts={
        UIComponentSlot='UniverseID component;const char* connection;',
        UIAction='uint32_t id;const char* text;const char* type;bool ispossible;bool istobedisplayed;',
        SofttargetDetails2='UniverseID softtargetID;const char* softtargetConnectionName;uint32_t messageID;',
        MessageDetails3='uint64_t poiID;UniverseID componentID;const char* messageType;const char* connectionName;bool isAssociative;bool isMissionTarget;bool isPriorityMissionTarget;bool showIndicator;bool hasAdditionalOffset;'}
    local fields={UIComponentSlot={'component','connection'},UIAction={'id','text','type','ispossible','istobedisplayed'},
        SofttargetDetails2={'softtargetID','softtargetConnectionName','messageID'},
        MessageDetails3={'poiID','componentID','messageType','connectionName','isAssociative','isMissionTarget','isPriorityMissionTarget','showIndicator','hasAdditionalOffset'}}
    for name,body in pairs(layouts)do
        if not pcall(ffi.typeof,name)then ffi.cdef('typedef struct {'..body..'} '..name..';')end
        local proof=ffi.typeof('struct {'..body..'}')
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),'actor ABI size')
        for _,key in ipairs(fields[name])do
            assert(ffi.offsetof(name,key)==ffi.offsetof(proof,key),'actor ABI offset')
            local a,b=ffi.new(name),ffi.new(proof)
            assert(type(a[key])==type(b[key]),'actor ABI field type')
            if type(a[key])=='cdata'then assert(ffi.typeof(a[key])==ffi.typeof(b[key]),'actor ABI cdata type')
            elseif type(a[key])=='number'then
                a[key]=4294967295;b[key]=4294967295
                assert(a[key]==4294967295 and ffi.string(ffi.cast('char*',a),ffi.sizeof(a))
                    ==ffi.string(ffi.cast('char*',b),ffi.sizeof(b)),'actor ABI uint32 representation')
            end
        end
    end
    ffi.cdef[[UniverseID GetPlayerID(void); UniverseID GetPlayerContainerID(void);
        UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);
        bool IsComponentClass(UniverseID componentid,const char* classname);
        bool SetSofttarget(UniverseID componentid,const char*const connectionname);
        SofttargetDetails2 GetSofttarget2(void); MessageDetails3 GetMessageDetails3(uint32_t messageid);
        uint32_t GetNumCompSlotPlayerActions(UIComponentSlot compslot);
        uint32_t GetCompSlotPlayerActions(UIAction* result,uint32_t resultlen,UIComponentSlot compslot);
        bool PerformCompSlotPlayerAction(UIComponentSlot compslot,uint32_t actionid);
        bool IsConversationActive(void); bool IsConversationCancelling(void);]]
    local profile=d.capacity_profile;if profile==nil then profile='interactive'end
    local limit,revision=128,'UNBOUND_LEGACY_INTERACTIVE'
    assert(profile=='interactive' or profile=='campaign_v1','unknown capacity profile')
    if d.capacity_resolver then
        local c=d.capacity_resolver(profile);assert(type(c)=='table' and c.name==profile
            and type(c.native_history)=='number' and c.native_history%1==0 and c.native_history>=1 and c.native_history<=512,'actor capacity')
        assert(type(c.revision)=='string' and #c.revision==64,'capacity revision');limit=c.native_history;revision=c.revision
    else assert(profile=='interactive','campaign resolver required')end
    local api,records,used,scans,latest,bound,last,retired,busy={}, {},0,0,nil,nil,nil,false,false
    local function cp(v)if type(v)~='table'then return v end;local o={};for k,x in pairs(v)do o[k]=cp(x)end;return o end
    local function eq(a,b)
        if type(a)~=type(b)then return false end;if type(a)~='table'then return a==b end
        for k,v in pairs(a)do if not eq(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end;return true
    end
    local function integer(v,a,b)return type(v)=='number' and v==v and v>=a and v<=b and v%1==0 end
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    local function context()
        local ok,s,t=pcall(function()
            assert(not retired,'actor entry retired');local s=d.current();local out,n={},0
            local keys={provider=true,revision=true,session=true,generation=true,world_token=true}
            assert(type(s)=='table' and getmetatable(s)==nil,'public identity unavailable')
            for k,v in pairs(s)do assert(keys[k],'unknown stamp field');out[k]=v;n=n+1 end
            assert(n==5 and s.provider=='x4-live-session-v1' and hex(s.revision,64) and hex(s.session,32)
                and hex(s.generation,32) and type(s.world_token)=='string' and #s.world_token>0 and #s.world_token<=512,'public stamp')
            assert(not bound or eq(bound,out),'public identity changed')
            local t=d.now();assert(type(t)=='number' and t==t and t>=0 and t<math.huge and (not last or t>=last),'UI clock changed')
            return out,t
        end)
        if not ok then if bound then retired=true end;error(s)end
        bound=cp(s);last=t;return s,t
    end
    local function id(v,zero)
        assert(type(v)=='cdata' and ffi.istype('uint64_t',v),'exact native identity')
        local s=tostring(v):match('^(%d+)ULL$');assert(s and (zero or s~='0'),'native identity absent');return s
    end
    local function str(p,n)
        assert(p~=nil and p~=ffi.NULL,'native text absent');local i=0
        while i<=n and p[i]~=0 do i=i+1 end;assert(i<=n,'native text bound')
        local s=ffi.string(p,i);assert(not s:find('[%z\1-\31]'),'native control text');return s
    end
    local function location()
        local p=C.GetPlayerID();local room=C.GetContextByClass(p,'room',false);local container=C.GetPlayerContainerID()
        return {player=id(p),room=id(room),container=id(container)},room
    end
    local function census()
        assert(type(g.GetPrioritizedPlatformNPCs)=='function' and type(g.ConvertStringTo64Bit)=='function','room NPC API unavailable')
        local loc,room=location();local converted=g.ConvertStringTo64Bit(tostring(room))
        assert(id(d.component_id(converted))==id(room),'room conversion changed identity')
        local raw=g.GetPrioritizedPlatformNPCs(converted)
        assert(type(raw)=='table' and getmetatable(raw)==nil and #raw<=128,'room NPC bound')
        local n=0;for k in pairs(raw)do assert(integer(k,1,#raw),'room NPC list sparse');n=n+1 end
        assert(n==#raw,'room NPC list sparse');local rows,seen={},{}
        for _,v in ipairs(raw)do
            local value=d.component_id(v);local key=id(value);assert(not seen[key],'duplicate native NPC');seen[key]=true
            assert(C.IsComponentClass(value,'npc')==true,'native candidate is not NPC')
            if C.GetContextByClass(value,'room',false)==room then rows[#rows+1]={id=key,value=value}end
        end
        table.sort(rows,function(a,b)return a.id<b.id end)
        return loc,rows,n
    end
    local function rowmodel(rows)local out={};for i,v in ipairs(rows)do out[i]=v.id end;return out end
    local function info(value)
        local raw=d.actor_info(value);assert(type(raw)=='table' and getmetatable(raw)==nil,'actor metadata unavailable')
        local out,n={},0;local keys={name=256,typestring=64,rolename=128,owner=64}
        for k,v in pairs(raw)do assert(keys[k] and type(v)=='string' and #v<=keys[k] and not v:find('[%z\1-\31]'),'actor metadata malformed');out[k]=v;n=n+1 end
        assert(n==4,'actor metadata incomplete');return out
    end
    local function membership(row)
        local loc,rows,n=census();assert(eq(loc,row.location),'actor location changed')
        local found=false;for _,v in ipairs(rows)do if v.id==row.id then found=true;assert(v.value==row.value,'actor handle changed')end end
        assert(found,'actor no longer in observed room');assert(eq(info(row.value),row.info),'actor metadata changed');return loc,n
    end
    local function target(row)
        local t=C.GetSofttarget2();assert(id(t.softtargetID)==row.id,'actor is not current softtarget')
        local message=tonumber(t.messageID);assert(integer(message,1,4294967295),'target message absent')
        local connection=str(t.softtargetConnectionName,128);local m=C.GetMessageDetails3(message)
        assert(id(m.componentID)==row.id and str(m.connectionName,128)==connection,'message target changed')
        assert(not m.isAssociative and not m.hasAdditionalOffset,'indirect target message')
        return {message=message,connection=connection,component=row.id,poi=id(m.poiID,true),
            kind=str(m.messageType,128),mission=m.isMissionTarget,priority=m.isPriorityMissionTarget,indicator=m.showIndicator}
    end
    local function actions(row,binding)
        local slot=ffi.new('UIComponentSlot');slot.component=row.value;slot.connection=binding.connection
        local n=tonumber(C.GetNumCompSlotPlayerActions(slot));assert(integer(n,0,32),'offered action bound')
        local out={};if n>0 then
            local buf=ffi.new('UIAction[?]',n);local filled=tonumber(C.GetCompSlotPlayerActions(buf,n,slot));assert(filled==n,'action count changed')
            local seen={};for i=0,n-1 do
                local a=buf[i];local key=tonumber(a.id);assert(integer(key,1,4294967295) and not seen[key],'action identity');seen[key]=true
                out[#out+1]={id=key,text=str(a.text,256),kind=str(a.type,64),possible=a.ispossible,displayed=a.istobedisplayed}
            end
        end
        assert(tonumber(C.GetNumCompSlotPlayerActions(slot))==n,'action count changed');return out,slot
    end
    local function instant(binding)
        assert(type(g.GetActionInfo)=='function' and type(g.PerformAction)=='function','CORE action API unavailable')
        local _,possible,n,instant_action,_,_,displayed=g.GetActionInfo(binding.message)
        assert(type(possible)=='boolean' and integer(n,1,32) and type(instant_action)=='boolean'
            and integer(displayed,1,32),'instant action model unavailable')
        assert(possible and instant_action,'instant action not offered')
        return {possible=possible,count=n,instant=instant_action,displayed=displayed}
    end
    local function ready()
        assert(C.IsConversationActive()==false and C.IsConversationCancelling()==false,'conversation already active/cancelling')
    end
    local function pack(v)
        assert(#d.encode({payload=d.encode(v),reserve=string.rep('x',900)})<=4000,'actor output wire bound');return v
    end
    local function finish(s,deadline)
        local a,t=context();assert(eq(a,s) and (not deadline or t<=deadline),'actor identity/TTL changed');return t
    end
    local function guarded(fn)
        local ok,v=pcall(fn);if ok then return v end
        return {status='UNAVAILABLE',reason=tostring(v):sub(1,384),conversation_verified=false,runtime_verified=false}
    end
    function api.observe(start)
        return guarded(function()
            if start==nil then start=1 end;assert(integer(start,1,129),'actor page start')
            local s,age=context();assert(scans<limit,'actor observation capacity');scans=scans+1
            local loc,rows,n=census();local loc2,rows2,n2=census()
            assert(eq(loc,loc2) and eq(rowmodel(rows),rowmodel(rows2)) and n==n2,'room census changed')
            local keys,out={},{};for i=start,math.min(#rows,start+7)do
                local key='actor:'..scans..':'..i;local r=rows[i];r.location=cp(loc);r.info=info(r.value);keys[key]=r
                out[#out+1]={actor=key,component=r.id,info=cp(r.info)}
            end
            local v={status='OBSERVED',scope='PRIORITIZED_CURRENT_ROOM_NATIVE_NPCS',location=loc,rows=out,
                start=start,returned=#out,matched=#rows,native_count=n,excluded_other_room=n-#rows,
                complete_actor_census=false,independent_pages=true,ui_age=age,runtime_verified=false}
            while not pcall(pack,v)do
                assert(#out>1,'one actor row exceeds wire bound');keys[out[#out].actor]=nil;table.remove(out);v.returned=#out
            end
            for _,r in pairs(keys)do membership(r)end
            finish(s);latest=keys;return v
        end)
    end
    function api.prepare(action,key)
        return guarded(function()
            local s,t=context();assert(not busy and used<limit,'actor mutation busy/capacity')
            assert(action=='focus' or action=='interact' or action=='comm','closed actor action')
            local row=latest and latest[key];assert(row,'unknown/expired observed actor')
            used=used+1;local ticket='actor-action:'..used
            local r={row=row,action=action,stamp=s,deadline=t+5,used=false};records[ticket]=r
            membership(row);ready()
            if action~='focus'then
                r.binding=target(row)
                if action=='interact'then r.offer=instant(r.binding);r.perform=g.PerformAction;r.get=g.GetActionInfo
                else
                    r.offer=actions(row,r.binding);local found
                    for _,a in ipairs(r.offer)do if a.kind=='comm'then assert(not found,'ambiguous offered comm');found=a end end
                    assert(found and found.possible and found.displayed,'comm is not offered');r.actionid=found.id
                end
            end
            local v=pack({status='PREPARED',ticket=ticket,actor=row.id,action=action,
                scope=action=='comm' and 'NATIVE_OFFERED_COMPONENT_SLOT_ACTION' or action=='interact' and 'NATIVE_INSTANT_ROOT_ACTION' or 'NATIVE_SOFTTARGET_SELECTION',
                conversation_verified=false,rendered_menu_verified=false,runtime_verified=false})
            membership(row);if r.binding then assert(eq(target(row),r.binding),'target changed')end
            finish(s,r.deadline);return v
        end)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r or r.used then return {status='REFUSED',action_attempted=false,reason='unknown or consumed ticket'}end
        r.used=true;local attempted=false
        local ok,v=pcall(function()
            context();assert(not busy,'actor entry busy');membership(r.row);ready()
            local slot
            if r.binding then
                assert(eq(target(r.row),r.binding),'target changed')
                if r.action=='interact'then
                    assert(g.GetActionInfo==r.get and g.PerformAction==r.perform and eq(instant(r.binding),r.offer),'instant offer changed')
                else local model;model,slot=actions(r.row,r.binding);assert(eq(model,r.offer),'offered actions changed')end
            end
            membership(r.row);ready();if r.binding then assert(eq(target(r.row),r.binding),'target changed')end
            finish(r.stamp,r.deadline)
            if r.binding then assert(eq(target(r.row),r.binding),'final target changed')end
            busy=true;attempted=true
            local result
            if r.action=='focus'then result=C.SetSofttarget(r.row.value,'')
            elseif r.action=='comm'then result=C.PerformCompSlotPlayerAction(slot,r.actionid)
            else r.perform(r.binding.message,1);result='VOID'end
            if r.action~='interact'then assert(type(result)=='boolean','native result unreadable')end
            finish(r.stamp,r.deadline)
            local out=pack({status='DISPATCHED_UNVERIFIED',actor=r.row.id,action=r.action,native_result=result,
                action_attempted=true,conversation_verified=false,offered_section_verified=false,
                rendered_menu_verified=false,economic_effect_verified=false,runtime_verified=false})
            finish(r.stamp,r.deadline);busy=false;return out
        end)
        if ok then return v end
        if attempted then retired=true end
        return {status=attempted and 'UNCERTAIN' or 'REFUSED',action_attempted=attempted,reason=tostring(v):sub(1,384),
            conversation_verified=false,runtime_verified=false}
    end
    function api.capacity()
        return guarded(function()
            local s,t=context();local out=pack({status='OBSERVED',profile=profile,revision=revision,requests_used=used,
                requests_limit=limit,observations_used=scans,observations_limit=limit,busy=busy,ui_age=t,
                reset_scope='ADAPTER_LIFETIME_NO_EVICTION',runtime_verified=false});finish(s);return out
        end)
    end
    function api.dispose()retired=true;return true end
    return api
end

-- Bounded native owned-ship orders. No transport, registration or implicit calls.
-- Source and runtime limits: docs/live-orders-qualification.md.
return function(d)
    local ffi,C,g=d.ffi,d.C,d.globals
    assert(ffi and C and type(g)=="table","native order dependencies")
    for _,k in ipairs({"resolve_issued","epoch","now"}) do assert(type(d[k])=="function","missing "..k) end
    for _,k in ipairs({"IsValidComponent","GetComponentData","ConvertStringToLuaID",
        "ConvertIDTo64Bit","SetOrderParam","GetOrderParams","IsDockingPossible"}) do
        assert(type(g[k])=="function","missing native global "..k)
    end
    local function declare(name,src)
        if not pcall(ffi.typeof,name) then ffi.cdef(src) end
    end
    declare("UniverseID","typedef uint64_t UniverseID;")
    declare("AIOrderID","typedef uint64_t AIOrderID;")
    for _,name in ipairs({"UniverseID","AIOrderID","size_t"}) do
        assert(ffi.sizeof(name)==8 and ffi.istype("uint64_t",ffi.new(name)),
            "native unsigned 64-bit alias required: "..name)
    end
    local fields=[[
        size_t queueidx; const char *state,*statename,*orderdef; size_t actualparams;
        bool enabled,isinfinite,issyncpointreached,istemporder;
    ]]
    declare("Order","typedef struct {"..fields.."} Order;")
    declare("Order3","typedef struct {"..fields.."bool isoverride,ispriority;} Order3;")
    local function layout(name,extra)
        local proof=ffi.typeof("struct {"..fields..extra.."}")
        assert(ffi.sizeof(name)==ffi.sizeof(proof) and ffi.alignof(name)==ffi.alignof(proof),
            "native order layout size")
        local names={"queueidx","state","statename","orderdef","actualparams",
            "enabled","isinfinite","issyncpointreached","istemporder"}
        if extra~="" then names[#names+1]="isoverride";names[#names+1]="ispriority" end
        local probe=ffi.new(name)
        for _,k in ipairs(names) do
            assert(ffi.offsetof(name,k)==ffi.offsetof(proof,k),"native order layout offset")
            if k=="queueidx" or k=="actualparams" then
                assert(ffi.istype("size_t",probe[k]),"native order size_t field")
            elseif k=="state" or k=="statename" or k=="orderdef" then
                assert(ffi.istype("const char *",probe[k]),"native order string field")
            else assert(type(probe[k])=="boolean","native order bool field") end
        end
    end
    layout("Order","");layout("Order3","bool isoverride,ispriority;")
    local posfields="float x,y,z,yaw,pitch,roll;"
    declare("UIPosRot","typedef struct {"..posfields.."} UIPosRot;")
    local posproof=ffi.typeof("struct {"..posfields.."}")
    assert(ffi.sizeof("UIPosRot")==ffi.sizeof(posproof) and
        ffi.alignof("UIPosRot")==ffi.alignof(posproof),"native position layout size")
    local posprobe=ffi.new("UIPosRot")
    for _,k in ipairs({"x","y","z","yaw","pitch","roll"}) do
        assert(ffi.offsetof("UIPosRot",k)==ffi.offsetof(posproof,k),"native position layout offset")
        posprobe[k]=1.25
        assert(posprobe[k]==1.25,"native position float field")
        posprobe[k]=16777217
        assert(posprobe[k]==16777216,"native position float precision")
    end
    ffi.cdef[[
        uint32_t GetNumOrders(UniverseID controllableid);
        uint32_t GetOrders3(Order3* result,uint32_t resultlen,UniverseID controllableid);
        bool GetDefaultOrder(Order* result,UniverseID controllableid);
        AIOrderID GetOrderID(UniverseID controllableid,size_t orderidx);
        size_t GetOrderQueueCurrentIdx(UniverseID controllableid);
        uint32_t CreateOrder3(UniverseID controllableid,const char* orderid,
            bool defaultorder,bool isoverride,bool istemp);
        bool EnableOrder(UniverseID controllableid,size_t idx);
        bool RemoveOrder(UniverseID controllableid,size_t idx,bool playercancelled,bool checkonly);
        bool IsOrderSelectableFor(const char* orderdefid,UniverseID controllableid);
        bool IsComponentClass(UniverseID componentid,const char* classname);
        bool IsComponentOperational(UniverseID componentid);
        UniverseID GetPlayerControlledShipID(void);
        UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetContextByClass(UniverseID componentid,const char* classname,bool includeself);
    ]]
    local api,records,serial={},{},0
    local MAX_SUBMISSIONS,MAX_CANCELLATIONS=128,128
    local function finite(n) return type(n)=="number" and n==n and math.abs(n)<math.huge end
    local function uint(n,max)
        if type(n)=="cdata" then
            assert(ffi.istype("uint64_t",n) or ffi.istype("uint32_t",n),"unknown native count type")
            assert(n<=max,"native count overflow");n=tonumber(n)
        end
        assert(finite(n) and n>=0 and n%1==0 and n<=max,"invalid native count")
        return n
    end
    local function id(v,zero)
        assert(type(v)=="cdata" and ffi.istype("uint64_t",v),"exact native identity required")
        local s=tostring(v):match("^(%d+)ULL$")
        assert(s and (zero or s~="0"),"missing native identity")
        return s
    end
    local function boolean(v) assert(type(v)=="boolean","unknown native boolean");return v end
    local function str(p)
        assert(p~=nil and p~=ffi.NULL,"missing native string")
        local s=ffi.string(p)
        assert(#s<=2048,"native order string overflow")
        return s
    end
    local function stamp()
        local e,t=d.epoch(),d.now()
        assert(type(e)=="string" and #e>0 and #e<=128 and finite(t) and t>=0,"unknown lifecycle")
        return e,t
    end
    local function resolved(token,kind)
        assert(type(token)=="string" and #token>0 and #token<=128,"issued token required")
        local v=d.resolve_issued(token,kind)
        id(v)
        assert(g.IsValidComponent(v)==true,"invalid issued component")
        return v
    end
    local function equal(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~="table" then return a==b end
        for k,v in pairs(a) do if not equal(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end
        return true
    end
    local function copied(v)
        if type(v)~="table" then return v end
        local out={}
        for k,x in pairs(v) do out[k]=copied(x) end
        return out
    end
    local function row(b,default)
        local r={queueidx=uint(b.queueidx,64),state=str(b.state),statename=str(b.statename),
            orderdef=str(b.orderdef),actualparams=uint(b.actualparams,128),
            enabled=boolean(b.enabled),infinite=boolean(b.isinfinite),
            syncpoint_reached=boolean(b.issyncpointreached),temporary=boolean(b.istemporder)}
        assert(r.orderdef~="","unknown order definition")
        if not default then r.override=boolean(b.isoverride);r.priority=boolean(b.ispriority) end
        return r
    end
    local function queue(ship)
        local n=uint(C.GetNumOrders(ship),64)
        local rows,ids,indices={},{},{}
        if n>0 then
            local buf=ffi.new("Order3[?]",n)
            assert(uint(C.GetOrders3(buf,n,ship),64)==n,"order count drift")
            for i=0,n-1 do
                local r=row(buf[i],false)
                assert(r.queueidx>0 and r.queueidx<=n and not indices[r.queueidx],"invalid queue index")
                indices[r.queueidx]=true
                r.id=id(C.GetOrderID(ship,r.queueidx))
                assert(not ids[r.id],"duplicate order identity");ids[r.id]=true
                rows[#rows+1]=r
            end
            table.sort(rows,function(a,b) return a.queueidx<b.queueidx end)
        end
        local db=ffi.new("Order")
        local has=boolean(C.GetDefaultOrder(db,ship))
        local default=has and row(db,true) or false
        local current
        if n>0 then
            current=uint(C.GetOrderQueueCurrentIdx(ship),64)
            assert(current<=n,"current index outside observed queue")
        end
        assert(uint(C.GetNumOrders(ship),64)==n,"order count drift")
        for _,r in ipairs(rows) do
            assert(id(C.GetOrderID(ship,r.queueidx))==r.id,"order identity drift")
        end
        assert(g.IsValidComponent(ship)==true,"ship disappeared")
        return {ship=id(ship),count=n,rows=rows,default=default,current=current,
            current_scope=n>0 and "NATIVE_INDEX" or "NOT_QUERIED_EMPTY_QUEUE",
            atomic=false,scope="ALL_QUEUED_ROWS_PLUS_DEFAULT",parameters_scope="SELECTED_ORDER_READBACK_ONLY"}
    end
    local function eligibility(ship,order,target)
        assert(C.IsComponentClass(ship,"ship")==true and C.IsComponentOperational(ship)==true,
            "subject is not an operational ship")
        assert(g.GetComponentData(ship,"isplayerowned")==true,"subject is not player owned")
        local pilot=g.GetComponentData(ship,"assignedpilot")
        assert(pilot~=nil and pilot~=false and pilot~=0,"no assigned pilot")
        local pilotid=id(g.ConvertIDTo64Bit(pilot))
        local controlled,occupied=C.GetPlayerControlledShipID(),C.GetPlayerOccupiedShipID()
        id(controlled,true);id(occupied,true)
        assert(ship~=controlled and ship~=occupied,"player-controlled or occupied ship")
        assert(C.IsOrderSelectableFor(order,ship)==true,"order not selectable")
        if order=="MoveWait" then
            assert(C.IsComponentClass(target,"sector")==true,"move target is not a sector")
        elseif order=="DockAndWait" then
            assert(target~=ship and g.IsDockingPossible(ship,target)==true,"native docking refused")
        elseif order=="Attack" then
            assert(target~=ship and C.IsComponentClass(target,"destructible")==true and
                C.IsComponentClass(target,"defensible")==true and
                g.GetComponentData(target,"isplayerowned")==false,"attack target refused")
            assert(target~=controlled and target~=occupied,"attack target occupied")
        else error("unsupported order") end
        return pilotid
    end
    local function idle(q)
        assert(q.count==0,"explicit require_empty policy")
        assert(q.default==false or (q.default.orderdef=="Wait" and not q.default.temporary),
            "non-idle default order preserved")
    end
    local function refuse(err)
        return {status="REFUSED",reason=tostring(err),action_attempted=false}
    end
    local function docking(ship)
        local subject=id(ship)
        local ok,value=pcall(function()
            -- Native MapMenu subordinate rows read this exact ship's docking flag
            -- and its containing ship/station, never a same-macro substitute.
            local docked=boolean(g.GetComponentData(ship,"isdocked"))
            local container=false
            if docked then
                local host=C.GetContextByClass(ship,"container",false)
                container=id(host)
                assert(host~=ship and g.IsValidComponent(host)==true,"docking container unavailable")
                assert(id(C.GetContextByClass(ship,"container",false))==container,"docking container changed")
            end
            assert(boolean(g.GetComponentData(ship,"isdocked"))==docked,"docking state changed")
            assert(g.IsValidComponent(ship)==true,"docking subject disappeared")
            return {status="OBSERVED",subject=subject,isdocked=docked,container=container,
                container_scope=docked and "NATIVE_IMMEDIATE_CONTAINER" or "NOT_QUERIED_UNDOCKED",
                atomic=false,order_caused_docking_verified=false}
        end)
        if ok then return value end
        return {status="UNKNOWN",subject=subject,reason=tostring(value):sub(1,256),
            atomic=false,order_caused_docking_verified=false}
    end
    function api.observe(ship_token)
        local ok,out=pcall(function()
            local e,t=stamp()
            local ship=resolved(ship_token,"ship")
            assert(C.IsComponentClass(ship,"ship")==true,"not a ship")
            local q=queue(ship)
            q.docking=docking(ship)
            assert(d.resolve_issued(ship_token,"ship")==ship and d.epoch()==e,"observation identity changed")
            q.status="OBSERVED";q.epoch=e;q.age=t;q.arrival_verified=false
            return q
        end)
        return ok and out or refuse(out)
    end
    function api.prepare(ship_token,action,target_token,policy,position)
        local ok,out=pcall(function()
            assert(policy=="require_empty","explicit supported queue policy required")
            local order=({move="MoveWait",dock="DockAndWait",attack="Attack"})[action]
            assert(order,"unsupported action")
            local e,t=stamp()
            local ship=resolved(ship_token,"ship")
            local target=resolved(target_token,action=="move" and "sector" or "object")
            local point,requested
            if action=="move" then
                assert(type(position)=="table" and #position==3,"exact xyz required")
                for k in pairs(position) do assert(k==1 or k==2 or k==3,"unexpected position field") end
                requested={}
                for i=1,3 do assert(finite(position[i]) and math.abs(position[i])<=10000000,"bounded finite coordinate");requested[i]=position[i] end
                local nativepoint=ffi.new("UIPosRot",requested)
                point={nativepoint.x,nativepoint.y,nativepoint.z}
            else assert(position==nil,"object orders take no position") end
            local pilot=eligibility(ship,order,target)
            local q=queue(ship);idle(q)
            assert(serial<MAX_SUBMISSIONS,"request history full")
            serial=serial+1
            local r={ticket="order-"..serial,epoch=e,age=t,ship=ship,target=target,
                ship_token=ship_token,target_token=target_token,order=order,action=action,
                policy=policy,point=point,pilot=pilot,before=q}
            records[r.ticket]=r
            return {status="PREPARED",ticket=r.ticket,epoch=e,age=t,ship=id(ship),
                target=id(target),order=order,policy=policy,before=copied(q),
                requested_position=requested,position=copied(point),position_format=point and "NATIVE_FLOAT32_XYZ" or nil,
                action_attempted=false,ui_route_verified=false}
        end)
        return ok and out or refuse(out)
    end
    local function param_readback(r,index)
        local params=g.GetOrderParams(r.ship,index)
        assert(type(params)=="table" and #params>=1 and #params<=128,"native parameters unavailable")
        for k in pairs(params) do
            assert(type(k)=="number" and k>=1 and k<=#params and k%1==0,"sparse native parameters")
        end
        local wanted=r.order=="Attack" and "primarytarget" or "destination"
        local matches=0
        for _,p in ipairs(params) do
            if p.name==wanted then
                matches=matches+1
                if r.order=="MoveWait" then
                    assert(p.type=="position" and type(p.value)=="table" and
                        id(g.ConvertIDTo64Bit(p.value[1]))==id(r.target) and
                        type(p.value[2])=="table","position readback identity")
                    local value=p.value[2]
                    local indexed=value[1]~=nil or value[2]~=nil or value[3]~=nil
                    local named=value.x~=nil or value.y~=nil or value.z~=nil
                    assert(indexed~=named,"ambiguous or missing position coordinates")
                    for i,k in ipairs({"x","y","z"}) do
                        assert(finite(indexed and value[i] or value[k]),"incomplete position coordinates")
                    end
                    local nativepoint=ffi.new("UIPosRot",value)
                    for i,k in ipairs({"x","y","z"}) do
                        assert(nativepoint[k]==r.point[i],"position readback mismatch")
                    end
                else assert(p.type=="object" and id(g.ConvertIDTo64Bit(p.value))==id(r.target),
                    "target readback mismatch") end
            end
        end
        assert(matches==1,"missing or duplicate destination parameter")
    end
    function api.execute(ticket)
        local r=records[ticket]
        if not r then return refuse("unknown ticket") end
        if r.consumed then return {status=r.status or "UNCERTAIN",reason="consumed; never retry",
            action_attempted=r.attempted==true,queue_index=r.index,order_id=r.order_id} end
        r.consumed=true
        local ok,err=pcall(function()
            local e,t=stamp()
            assert(e==r.epoch and t>=r.age and t-r.age<=30,"stale order lifecycle")
            assert(resolved(r.ship_token,"ship")==r.ship and
                resolved(r.target_token,r.action=="move" and "sector" or "object")==r.target,"issued identity changed")
            assert(eligibility(r.ship,r.order,r.target)==r.pilot,"assigned pilot changed")
            local before=queue(r.ship);idle(before)
            assert(equal(before,r.before),"queue/default changed")
            r.attempted=true
            local index=uint(C.CreateOrder3(r.ship,r.order,false,false,false),64)
            if index==0 then r.status="NOT_CREATED";return end
            r.index=index
            r.order_id=id(C.GetOrderID(r.ship,index))
            local created=queue(r.ship)
            -- The id read back from OUR CreateOrder3 index, present exactly once with our
            -- definition, is our creation: bind it for cancellation BEFORE the stricter
            -- checks below, so a failure there cannot leave it in the queue uncancellable.
            local own=0
            for _,v in ipairs(created.rows) do
                if v.id==r.order_id and v.orderdef==r.order then own=own+1 end
            end
            if own==1 then r.created_bound=true end
            assert(created.count==1 and created.rows[1].queueidx==index and
                created.rows[1].id==r.order_id and created.rows[1].orderdef==r.order and
                created.rows[1].enabled==false and equal(created.default,r.before.default),
                "created queue is not the sole disabled requested order")
            -- (created_bound set above; this stricter proof gates further dispatch only)
            local dest=g.ConvertStringToLuaID(tostring(r.target))
            if r.point then dest={dest,{r.point[1],r.point[2],r.point[3]}} end
            g.SetOrderParam(r.ship,index,1,nil,dest)
            param_readback(r,index)
            assert(eligibility(r.ship,r.order,r.target)==r.pilot and d.epoch()==r.epoch,
                "eligibility changed after creation")
            assert(id(C.GetOrderID(r.ship,index))==r.order_id,"created order rebound")
            local enabled=C.EnableOrder(r.ship,index)
            assert(type(enabled)=="boolean","unknown enable result")
            r.enable_return=enabled
            r.after=queue(r.ship)
            assert(r.after.count==1 and equal(r.after.default,r.before.default),
                "unexpected queue/default change after enable")
            local found
            for _,v in ipairs(r.after.rows) do if v.id==r.order_id then found=v end end
            assert(found and found.orderdef==r.order and found.queueidx==index,"submitted order not bound")
            param_readback(r,index)
            assert(d.epoch()==r.epoch and resolved(r.ship_token,"ship")==r.ship,
                "post-dispatch world or ship changed")
            r.status=enabled and found.enabled and "ORDER_OBSERVED" or "PARTIAL_CREATED_NOT_ENABLED"
        end)
        if not ok then r.status=r.attempted and "UNCERTAIN" or "REFUSED" end
        return {status=r.status,reason=ok and "native state observed; arrival not proved" or tostring(err),
            ticket=ticket,action_attempted=r.attempted==true,queue_index=r.index,order_id=r.order_id,
            enable_return=r.enable_return,after=r.after,arrival_verified=false,
            ui_route_verified=false,automatic_retry=false,automatic_cleanup=false}
    end
    -- Exact cancellation is restricted to a proven creation in this adapter instance.
    -- Native analogue: menu_map.lua5507-5511 checkonly then RemoveOrder(false,false).
    local cancels,cancelserial={},0
    local function cancel_eligible(ship)
        assert(C.IsComponentClass(ship,"ship")==true and C.IsComponentOperational(ship)==true,
            "cancel subject is not an operational ship")
        assert(g.GetComponentData(ship,"isplayerowned")==true,"cancel subject not player owned")
        local pilot=g.GetComponentData(ship,"assignedpilot")
        assert(pilot~=nil and pilot~=false and pilot~=0,"cancel subject has no pilot")
        local pilotid=id(g.ConvertIDTo64Bit(pilot))
        local controlled,occupied=C.GetPlayerControlledShipID(),C.GetPlayerOccupiedShipID()
        id(controlled,true);id(occupied,true)
        assert(ship~=controlled and ship~=occupied,"cancel subject player-controlled or occupied")
        return pilotid
    end
    local function locate_owned(q,submission)
        local match
        for _,v in ipairs(q.rows) do
            if v.id==submission.order_id then
                assert(not match and v.orderdef==submission.order,"journaled order identity/definition mismatch")
                match=v
            end
        end
        assert(match,"journaled order no longer present")
        return match
    end
    local function remaining_rows(q,removed)
        local out={}
        for _,v in ipairs(q.rows) do
            if v.id~=removed then
                local row=copied(v)
                row.queueidx=nil -- only this native reindexing is allowed
                out[#out+1]=row
            end
        end
        return out
    end
    function api.prepare_cancel(submission_ticket,expected_order_id)
        local ok,out=pcall(function()
            local s=records[submission_ticket]
            assert(s and s.consumed and s.created_bound and s.order_id and
                expected_order_id==s.order_id,"only exact locally journaled creation can be cancelled")
            assert(not s.cancel_attempted,"cancellation already attempted; no retry")
            local e,t=stamp()
            assert(e==s.epoch,"submission belongs to another lifecycle")
            local ship=resolved(s.ship_token,"ship")
            assert(ship==s.ship,"submission ship changed")
            local pilot=cancel_eligible(ship)
            local before=queue(ship)
            local target=locate_owned(before,s)
            assert(C.RemoveOrder(ship,target.queueidx,false,true)==true,"native cancellation unavailable")
            assert(equal(queue(ship),before) and d.epoch()==e,"queue or lifecycle changed during cancellation preparation")
            assert(cancelserial<MAX_CANCELLATIONS,"cancellation history full")
            cancelserial=cancelserial+1
            local ticket="cancel-"..cancelserial
            cancels[ticket]={submission=s,epoch=e,age=t,ship=ship,pilot=pilot,before=before,
                order_id=s.order_id,index=target.queueidx,ticket=ticket}
            return {status="PREPARED",ticket=ticket,submission_ticket=submission_ticket,
                epoch=e,age=t,ship=id(ship),order_id=s.order_id,queue_index=target.queueidx,
                before=copied(before),action_attempted=false,automatic_retry=false,
                preservation_scope="OBSERVED_QUEUE_ROWS_AND_DEFAULT; PARAMETERS_NOT_ENUMERATED"}
        end)
        return ok and out or refuse(out)
    end
    function api.execute_cancel(ticket)
        local r=cancels[ticket]
        if not r then return refuse("unknown cancellation ticket") end
        if r.consumed then return {status=r.status or "UNCERTAIN",reason="consumed; never retry",
            action_attempted=r.attempted==true,order_id=r.order_id,queue_index=r.index} end
        r.consumed=true
        local ok,err=pcall(function()
            local e,t=stamp()
            assert(e==r.epoch and t>=r.age and t-r.age<=30,"stale cancellation lifecycle")
            local s=r.submission
            assert(not s.cancel_attempted and s.created_bound and s.order_id==r.order_id,
                "journaled cancellation already attempted or rebound")
            assert(resolved(s.ship_token,"ship")==r.ship and cancel_eligible(r.ship)==r.pilot,
                "cancellation subject or pilot changed")
            local before=queue(r.ship)
            assert(equal(before,r.before),"queue/default changed after cancellation preparation")
            local target=locate_owned(before,s)
            assert(target.queueidx==r.index and C.RemoveOrder(r.ship,r.index,false,true)==true,
                "native cancellation eligibility changed")
            assert(equal(queue(r.ship),before) and d.epoch()==e and
                id(C.GetOrderID(r.ship,r.index))==r.order_id,"order changed before cancellation")
            r.attempted=true;s.cancel_attempted=true -- reserve before mutation, including exceptions
            r.remove_return=C.RemoveOrder(r.ship,r.index,false,false)
            r.after=queue(r.ship)
            assert(type(r.remove_return)=="boolean","unknown native removal result")
            local remains=false
            for _,v in ipairs(r.after.rows) do if v.id==r.order_id then remains=true end end
            r.target_present=remains
            assert(equal(r.after.default,before.default) and
                equal(remaining_rows(r.after,r.order_id),remaining_rows(before,r.order_id)),
                "other observed queue rows/default changed")
            assert(d.epoch()==e and resolved(s.ship_token,"ship")==r.ship and
                cancel_eligible(r.ship)==r.pilot,"cancellation context changed after native call")
            if r.remove_return and not remains and r.after.count==before.count-1 then
                r.status="ORDER_REMOVAL_OBSERVED"
            elseif not r.remove_return and remains and equal(r.after,before) then
                r.status="NOT_REMOVED"
            else error("native result and target presence do not establish exact removal") end
        end)
        if not ok then r.status=r.attempted and "UNCERTAIN" or "REFUSED" end
        return {status=r.status,reason=ok and "exact order removal readback; no arrival claim" or tostring(err),
            ticket=ticket,action_attempted=r.attempted==true,order_id=r.order_id,
            queue_index=r.index,remove_return=r.remove_return,target_present=r.target_present,
            after=r.after,preservation_scope="OBSERVED_QUEUE_ROWS_AND_DEFAULT; PARAMETERS_NOT_ENUMERATED",
            automatic_retry=false,automatic_cleanup=false,arrival_verified=false}
    end
    function api.capacity()
        return {available=true,budgets={submissions={used=serial,limit=MAX_SUBMISSIONS},
            cancellations={used=cancelserial,limit=MAX_CANCELLATIONS}},
            cached_context='PER_REQUEST_NOT_EXPOSED',lifecycle_gate='OWNING_ASSEMBLY'}
    end
    return api
end

-- Closed SDK adapter for the reviewed native menu/order/camera modules.
-- Native calls and eligibility remain in those modules (see their qualification docs).
-- No registration side effects, transport, arbitrary evaluation or automatic cleanup.
return function(d)
    assert(type(d)=="table" and type(d.revision)=="string" and #d.revision==64
        and not d.revision:find("[^0-9a-f]"),"bundle revision required")
    for _,k in ipairs({"public_current","resolve_issued","now"}) do
        assert(type(d[k])=="function","missing provider dependency: "..k)
    end
    local adapters={}
    for name,methods in pairs({menu={"prepare","prepare_open","execute","observe"},
        orders={"prepare","execute","prepare_cancel","execute_cancel","observe"},
        camera={"prepare","execute","observe","restore_cockpit"},transactions={"capture"},
        options={"prepare_open","prepare_submenu","prepare_select","execute","observe"},
        navigation={"prepare","execute","observe"},timecontrol={"prepare","execute","observe"}}) do
        assert(type(d[name])=="table","missing native adapter: "..name)
        adapters[name]={}
        for _,method in ipairs(methods) do
            assert(type(d[name][method])=="function","missing native method: "..name.."."..method)
            adapters[name][method]=d[name][method]
        end
    end
    if d.options.prepare_button~=nil then
        assert(type(d.options.prepare_button)=='function','invalid options button adapter')
        adapters.options.prepare_button=d.options.prepare_button
    end
    for _,method in ipairs({'prepare_close','observe_closed'})do
        if d.options[method]~=nil then
            assert(type(d.options[method])=='function','invalid options close adapter')
            adapters.options[method]=d.options[method]
        end
    end
    local current,resolve,clock,revision=d.public_current,d.resolve_issued,d.now,d.revision
    local capacity_name=d.capacity_profile
    if capacity_name==nil then capacity_name='interactive' end
    assert(capacity_name=='interactive' or capacity_name=='campaign_v1','unknown fixed capacity profile')
    local capacity={name='interactive',native_history=128,revision='UNBOUND_LEGACY_INTERACTIVE'}
    if d.capacity_resolver~=nil or capacity_name~='interactive' then
        assert(type(d.capacity_resolver)=='function','trusted capacity resolver required')
        capacity=d.capacity_resolver(capacity_name)
        assert(type(capacity)=='table' and capacity.name==capacity_name and type(capacity.revision)=='string'
            and #capacity.revision==64 and not capacity.revision:find('[^0-9a-f]') and
            type(capacity.native_history)=='number' and capacity.native_history%1==0 and
            capacity.native_history>0 and capacity.native_history<=512,'fixed native capacity manifest required')
        capacity={name=capacity.name,native_history=capacity.native_history,revision=capacity.revision}
    end
    local api,records,count,retired,bound={}, {},0,false,nil
    local actions={["menu.open"]=true,["menu.select"]=true,["menu.add"]=true,
        ["menu.confirm"]=true,["order.submit"]=true,["order.cancel"]=true,["camera.view"]=true,
        ["camera.restore"]=true,["options.open"]=true,["options.submenu"]=true,["options.select"]=true,
        ["options.button"]=true,["options.close"]=true}
    actions["menu.view"]=true;actions["menu.close"]=true
    actions["menu.repair_tab"]=true;actions["menu.repair_select"]=true
    for _,name in ipairs({"guidance","target","autopilot_start","dock_request","quick_dock"}) do
        actions["navigation."..name]=true
    end
    actions["time.start"]=true;actions["time.stop"]=true
    local function hex(s,n)
        return type(s)=="string" and #s==n and not s:find("[^0-9a-f]")
    end
    local function text(s)
        return type(s)=="string" and #s>0 and #s<=128 and not s:find("[^%w_.:-]")
    end
    local function finite(v)
        return type(v)=="number" and v==v and v>=0 and v<math.huge
    end
    local function now()
        local t=clock();assert(finite(t),"provider clock unavailable");return t
    end
    local function identity()
        assert(not retired,"provider retired")
        local s=current()
        assert(type(s)=="table" and s.provider=="x4-live-session-v1"
            and hex(s.revision,64) and hex(s.session,32) and hex(s.generation,32)
            and type(s.world_token)=="string" and #s.world_token>0 and #s.world_token<=128,
            "public identity unavailable")
        local n=0;for k in pairs(s) do
            assert(k=="provider" or k=="revision" or k=="session" or k=="generation" or k=="world_token",
                "unexpected public identity field");n=n+1
        end
        assert(n==5,"incomplete public identity")
        if bound then
            for k,v in pairs(bound) do
                if s[k]~=v then retired=true;error("public identity changed") end
            end
        else bound={};for k,v in pairs(s) do bound[k]=v end end
        return s
    end
    local function id(v)
        local s=type(v)=="string" and v or type(v)=="cdata" and tostring(v):match("^(%d+)ULL$")
        assert(s and s:match("^[1-9]%d*$") and #s<=20
            and (#s<20 or s<="18446744073709551615"),"issued component identity unavailable")
        return s
    end
    local function arguments(a,required,optional)
        assert(type(a)=="table" and getmetatable(a)==nil,"plain arguments required")
        for k,v in pairs(a) do
            assert(required[k] or optional and optional[k],"unexpected action argument")
            assert(type(v)=="string" or type(v)=="number" or type(v)=="boolean","flat scalar arguments required")
        end
        for k in pairs(required) do assert(a[k]~=nil,"missing action argument: "..k) end
    end
    local function validate(req)
        assert(req.provider=="x4-native-v1" and actions[req.action] and req.binding==revision,
            "unbound provider/action/revision")
        assert(hex(req.id,32) and hex(req.digest,64),"request identity required")
        local a=req.arguments
        if req.action=="time.start" or req.action=="time.stop" then
            arguments(a,{ship=true});assert(text(a.ship),"issued occupied ship required")
        elseif req.action:sub(1,11)=="navigation." then
            arguments(a,{ship=true,target=true})
            assert(text(a.ship) and text(a.target),"issued player ship and target required")
        elseif req.action=="options.open" then
            arguments(a,{})
        elseif req.action=="options.close" then
            arguments(a,{page=true});assert(text(a.page),'observed options page required')
        elseif req.action=="options.submenu" then
            arguments(a,{page=true,id=true,submenu=true})
            assert(a.page=="main" or a.page=="simple_menu_extension_options","closed submenu entry page")
            assert(text(a.id) and text(a.submenu),"observed submenu identities required")
        elseif req.action=="options.select" then
            arguments(a,{page=true,row=true,col=true,widget=true,option=true,text=true,value_type=true,value=true})
            assert(text(a.page),"observed options page required")
            for k,cap in pairs({row=512,col=64,widget=2147483647}) do
                assert(finite(a[k]) and a[k]%1==0 and a[k]>=1 and a[k]<=cap,"bounded native coordinates required")
            end
            assert(type(a.option)=="string" and #a.option>0 and #a.option<=256
                and not a.option:find("[%c]"),"native option identity required")
            for _,k in ipairs({"text","value"}) do
                assert(type(a[k])=="string" and #a[k]<=4096 and not a[k]:find("%z"),"bounded displayed value required")
            end
            assert(a.value_type=="number" or a.value_type=="string" or a.value_type=="boolean","explicit authored value type")
        elseif req.action=="options.button" then
            arguments(a,{page=true,row=true,col=true,widget=true,id=true,text=true})
            assert(text(a.page) and text(a.id),'observed button page/id required')
            for k,cap in pairs({row=512,col=64,widget=2147483647})do
                assert(finite(a[k]) and a[k]%1==0 and a[k]>=1 and a[k]<=cap,'bounded button coordinates')
            end
            assert(type(a.text)=='string' and #a.text<=4096 and not a.text:find('%z'),'bounded button text')
        elseif req.action=="menu.view" then
            arguments(a,{ship=true});assert(text(a.ship),"issued owned ship required")
        elseif req.action=="menu.close" then
            arguments(a,{opening=true});assert(hex(a.opening,32),"owned opening request required")
        elseif req.action=="menu.repair_tab" or req.action=="menu.repair_select" then
            arguments(a,{station=true,ship=true})
            assert(text(a.station) and text(a.ship),"issued repair station and ship required")
        elseif req.action=="menu.open" then
            arguments(a,{mode=true,station=true},{ship=true})
            assert((a.mode=="purchase" and a.ship==nil) or (a.mode=="upgrade" and text(a.ship)),
                "explicit purchase or single-ship upgrade")
            assert(text(a.station),"issued station token required")
        elseif req.action=="menu.select" or req.action=="menu.add" or req.action=="menu.confirm" then
            local keys={mode=true,station=true}
            if req.action=="menu.select" then keys.field=true;keys.option=true end
            arguments(a,keys)
            assert((a.mode=="purchase" or a.mode=="upgrade") and text(a.station),"explicit menu context")
            if req.action=="menu.select" then
                assert(a.field=="class" or a.field=="ship" or a.field=="loadout","closed menu selector")
                assert(type(a.option)=="string" and #a.option>0 and #a.option<=256
                    and not a.option:find("[%c]"),"native option identity required")
            end
        elseif req.action=="order.submit" then
            arguments(a,{ship=true,kind=true,target=true,policy=true},{x=true,y=true,z=true})
            assert(text(a.ship) and text(a.target) and a.policy=="require_empty","issued subject/empty policy")
            assert(a.kind=="move" or a.kind=="dock" or a.kind=="attack","closed native order")
            if a.kind=="move" then
                for _,k in ipairs({"x","y","z"}) do
                    local v=a[k];assert(type(v)=="number" and v==v and math.abs(v)<=10000000,"bounded xyz required")
                end
            else assert(a.x==nil and a.y==nil and a.z==nil,"object order takes no position") end
        elseif req.action=="order.cancel" then
            arguments(a,{submission=true,order_id=true})
            assert(hex(a.submission,32),"owned submission request required");id(a.order_id)
        elseif req.action=="camera.restore" then
            arguments(a,{view=true});assert(hex(a.view,32),"owned view request required")
        else arguments(a,{target=true});assert(text(a.target),"issued target required") end
    end
    local function snapshot_request(req)
        local out={id=req.id,digest=req.digest,action=req.action,session=req.session,binding=req.binding,arguments={}}
        for k,v in pairs(req.arguments) do out.arguments[k]=v end
        return out
    end
    local function matches(a,b)
        for _,k in ipairs({"id","digest","action","session","binding"}) do if a[k]~=b[k] then return false end end
        for k,v in pairs(a.arguments) do if b.arguments[k]~=v then return false end end
        for k in pairs(b.arguments) do if a.arguments[k]==nil then return false end end
        return true
    end
    local function output(r,state,reason,native,scope)
        local o={scope=scope or "ACTION_OBSERVATIONS_ONLY",completion_qualified=state=="completed",
            construction_verified=false,delivery_verified=false,arrival_verified=false,
            economic_outcome_verified=false,setting_effect_verified=false,rendered_pixels_verified=false,
            wrapper_dispatch_attempted=r and r.dispatched or false}
        if native then
            assert(type(native)=="table" and type(native.status)=="string","malformed native observation")
            o.native_status=native.status
            o.native_action_attempted="UNKNOWN"
            if type(native.action_attempted)=="boolean" then o.native_action_attempted=native.action_attempted end
            if type(native.native_pause_possible)=="boolean" then o.native_pause_possible=native.native_pause_possible end
            -- A rolled-back order creation says why, and where an unremoved order is left.
            if (native.status=="ROLLED_BACK" or native.status=="ROLLBACK_FAILED") and type(native.reason)=="string" then
                o.native_reason=native.reason:sub(1,512)
                if type(native.queue_index)=="number" then o.queue_index=native.queue_index end
            end
            for _,k in ipairs({"order_id","ship","container","mode"}) do
                if type(native[k])=="string" and #native[k]<=128 then o[k]=native[k] end
            end
            if native.attempts then
                assert(type(native.attempts)=="table","malformed native attempt facts")
                for key,field in pairs({softtarget="softtarget_attempted",
                    target_view="target_view_attempted",cockpit="cockpit_attempted"}) do
                    o[field]="UNKNOWN"
                    if type(native.attempts[key])=="boolean" then o[field]=native.attempts[key] end
                end
            end
        end
        return {status=state,reason=reason,observations=o}
    end
    local function prepare(r)
        local a=r.request.arguments
        if r.request.action=="time.start" or r.request.action=="time.stop" then
            return adapters.timecontrol.prepare(r.request.action:sub(6),a.ship)
        elseif r.request.action:sub(1,11)=="navigation." then
            return adapters.navigation.prepare(r.request.action:sub(12),a.ship,a.target)
        elseif r.request.action=="options.open" then
            return adapters.options.prepare_open()
        elseif r.request.action=="options.close" then
            if type(adapters.options.prepare_close)~='function' or type(adapters.options.observe_closed)~='function' then
                return {status='REFUSED',reason='options close adapter unavailable',action_attempted=false}
            end
            return adapters.options.prepare_close(a.page)
        elseif r.request.action=="options.submenu" then
            return adapters.options.prepare_submenu(a.page,a.id,a.submenu)
        elseif r.request.action=="options.select" then
            return adapters.options.prepare_select(a.page,a.row,a.col,a.widget,a.option,a.text,a.value_type,a.value)
        elseif r.request.action=="options.button" then
            if type(adapters.options.prepare_button)~='function' then
                return {status='REFUSED',reason='options button adapter unavailable',action_attempted=false}
            end
            return adapters.options.prepare_button(a.page,a.row,a.col,a.widget,a.id,a.text)
        elseif r.request.action=="menu.view" then
            return adapters.menu.prepare_open("view",nil,a.ship)
        elseif r.request.action=="menu.close" then
            local opened=records[a.opening]
            assert(opened and (opened.request.action=="menu.open" or opened.request.action=="menu.view")
                and opened.dispatched and opened.dispatch_readback_qualified==true
                and opened.executed and opened.executed.status=="DISPATCHED_UNVERIFIED"
                and opened.ticket,"successful owned menu opening required")
            return adapters.menu.prepare("close",nil,{opening=opened.ticket})
        elseif r.request.action=="menu.repair_tab" or r.request.action=="menu.repair_select" then
            local station=id(resolve(a.station,"station"));local ship=id(resolve(a.ship,"ship"));identity()
            return adapters.menu.prepare(r.request.action:sub(6),nil,{mode="upgrade",container=station,ship=ship})
        elseif r.request.action=="menu.open" then
            return adapters.menu.prepare_open(a.mode,a.station,a.ship)
        elseif r.request.action:sub(1,5)=="menu." then
            local station=id(resolve(a.station,"station"))
            identity()
            return adapters.menu.prepare(r.request.action=="menu.select" and a.field
                or r.request.action:sub(6),a.option,{mode=a.mode,container=station})
        elseif r.request.action=="order.submit" then
            local point=a.kind=="move" and {a.x,a.y,a.z} or nil
            return adapters.orders.prepare(a.ship,a.kind,a.target,a.policy,point)
        elseif r.request.action=="order.cancel" then
            local s=records[a.submission]
            assert(s and s.request.action=="order.submit" and s.dispatched and s.ticket
                and s.executed and s.executed.order_id==a.order_id,"only exact owned creation")
            r.submission=s
            return adapters.orders.prepare_cancel(s.ticket,a.order_id)
        elseif r.request.action=="camera.restore" then
            local view=records[a.view]
            assert(view and view.request.action=="camera.view" and view.camera_verified
                and not view.restore_attempted,"only an owned verified view can restore")
            r.view=view
            return {status="PREPARED",ticket=view.ticket,action_attempted=false}
        end
        return adapters.camera.prepare(a.target)
    end
    local function execute(r)
        local action=r.request.action
        if action=="time.start" or action=="time.stop" then return adapters.timecontrol.execute(r.ticket) end
        if action:sub(1,11)=="navigation." then return adapters.navigation.execute(r.ticket) end
        if action:sub(1,8)=="options." then return adapters.options.execute(r.ticket) end
        if action:sub(1,5)=="menu." then return adapters.menu.execute(r.ticket) end
        if action=="order.submit" then return adapters.orders.execute(r.ticket) end
        if action=="order.cancel" then return adapters.orders.execute_cancel(r.ticket) end
        if action=="camera.restore" then
            assert(not r.view.restore_attempted,"restoration already attempted")
            r.view.restore_attempted=true
            return adapters.camera.restore_cockpit(r.ticket)
        end
        return adapters.camera.execute(r.ticket)
    end
    local function status(r)
        assert(r.finished_at and now()>=r.finished_at,"dispatch timing unavailable or clock reversed")
        local action=r.request.action
        if action=="time.start" or action=="time.stop" then
            local observed=adapters.timecontrol.observe(r.request.arguments.ship);identity()
            return output(r,observed.status=="OBSERVED" and "running" or "unknown",
                "native SETA state is not qualified acceleration or invariant outcomes",observed,"NATIVE_SETA_STATE_ONLY")
        elseif action:sub(1,11)=="navigation." then
            local a=r.request.arguments
            local observed=adapters.navigation.observe(a.ship,a.target);identity()
            return output(r,observed.status=="OBSERVED" and "running" or "unknown",
                "navigation state requires separate causal arrival evidence",observed,"PLAYER_NAVIGATION_STATE_ONLY")
        elseif action=="options.close" then
            local observed=adapters.options.observe_closed(r.ticket);identity()
            if observed.status=='MENU_CLOSED_OBSERVED' and now()>r.finished_at then
                return output(r,'completed','later OptionsMenu shown=false readback; no pixel or pause claim',observed,
                    'OWNED_OPTIONS_CLOSED_READBACK')
            end
            return output(r,'unknown','owned OptionsMenu closure not yet observed',observed)
        elseif action:sub(1,8)=="options." then
            local a=r.request.arguments
            local page=action=="options.open" and "main" or action=="options.submenu" and a.submenu or a.page
            local observed=adapters.options.observe(page);identity()
            return output(r,observed.status=="OBSERVED" and "running" or "unknown",
                "built options model is not proof of the setting effect",observed,"BUILT_OPTIONS_MODEL_ONLY")
        elseif action=="menu.close" then
            local observed=adapters.menu.observe(r.ticket);identity()
            if observed.status=="MENU_CLOSED_OBSERVED" and now()>r.finished_at then
                return output(r,"completed","later owned menu visibility flag is false; no rendered or economic claim",observed,
                    "OWNED_MENU_CLOSED_READBACK")
            end
            return output(r,"unknown","owned menu closure is not yet observed",observed)
        elseif action=="camera.view" then
            local observed=adapters.camera.observe(r.ticket)
            identity()
            if observed.status=="TARGET_VERIFIED" then
                r.camera_verified=true
                return output(r,"completed","later external camera target readback; no pixel proof",observed,
                    "CAMERA_TARGET_READBACK_AT_OBSERVATION")
            end
            return output(r,observed.status=="PENDING" and "running" or "unknown",
                "camera completion not established",observed)
        elseif action=="camera.restore" then
            return output(r,"unknown","no attested cockpit-mode getter; restoration unverified",r.executed)
        elseif action=="order.submit" or action=="order.cancel" then
            local ship=action=="order.submit" and r.request.arguments.ship or r.submission.request.arguments.ship
            local observed=adapters.orders.observe(ship);identity()
            if observed.status~="OBSERVED" then return output(r,"unknown","order observation unavailable",observed) end
            if action=="order.cancel" and r.executed and r.executed.status=="ORDER_REMOVAL_OBSERVED"
                and now()>r.finished_at then
                assert(observed.ship==r.prepared.ship and type(observed.rows)=="table"
                    and observed.count==#observed.rows,"unbound cancellation readback")
                for _,row in ipairs(observed.rows) do
                    if row.id==r.request.arguments.order_id then
                        return output(r,"unknown","removed order is present again",observed)
                    end
                end
                return output(r,"completed","owned removal followed by later queue absence",observed,
                    "OWNED_ORDER_REMOVAL_AT_OBSERVATION")
            end
            return output(r,"running","queue observation is not arrival or completion",observed)
        end
        local observed=adapters.menu.observe();identity()
        return output(r,observed.status=="OBSERVED" and "running" or "unknown",
            "current menu observation is not economic completion",observed)
    end
    local function invoke(phase,req,done)
        assert(type(done)=="function","callback required")
        local r=records[req.id]
        local ok,value=pcall(function()
            local s=identity();assert(s.session==req.session,"request belongs to another public session")
            validate(req)
            if r then assert(matches(r.request,req),"changed owned request") end
            if phase=="prepare" then
                assert(not r,"duplicate native preparation")
                assert(count<capacity.native_history,"native request capacity exhausted")
                r={request=snapshot_request(req),dispatched=false}
                records[req.id]=r;count=count+1
                local p=prepare(r);identity()
                assert(type(p)=="table" and type(p.status)=="string","malformed native preparation")
                r.prepared=p
                if p.status~="PREPARED" then return output(r,"refused","native preparation refused",p) end
                assert(p.ticket~=nil,"native preparation missing private ticket")
                r.ticket=p.ticket
                return output(r,"prepared","private preparation; execute revalidates native state",p)
            end
            assert(r and r.ticket,"no private preparation")
            if phase=="execute" then
                assert(not r.dispatched,"native execution already attempted")
                r.dispatched=true -- reserve before native call, including exceptions
                local started=now()
                local result=execute(r)
                r.executed=result
                local finished=now();assert(finished>=started,"clock reversed during dispatch")
                r.finished_at=finished;identity()
                assert(type(result)=="table" and type(result.status)=="string","malformed native result")
                local state="unknown"
                if result.status=="REFUSED" and result.action_attempted==false then state="refused"
                elseif result.status=="DISPATCHED_UNVERIFIED" or result.status=="ORDER_OBSERVED"
                    or result.status=="PARTIAL_CREATED_NOT_ENABLED" or result.status=="REQUESTED"
                    or result.status=="ORDER_REMOVAL_OBSERVED" then state="running"
                elseif result.status=="NOT_CREATED" or result.status=="NOT_REMOVED" or result.status=="NATIVE_REFUSED"
                    or result.status=="ROLLED_BACK" then state="failed" end
                -- ROLLBACK_FAILED stays "unknown": the order is left in the queue (reported).
                local response=output(r,state,"native dispatch/readback; reconcile without repeating execute",result)
                r.dispatch_readback_qualified=true
                return response
            end
            assert(phase=="status","unsupported phase")
            if not r.dispatched then
                return output(r,"prepared","private preparation retained; native execute revalidates",r.prepared)
            end
            return status(r)
        end)
        if not ok then
            value=output(r,r and "unknown" or "refused",tostring(value))
        end
        done(value)
    end
    function api.session()
        local ok,s=pcall(identity)
        if not ok then return nil end
        return s.session
    end
    function api.dispose() retired=true;return true end
    function api.capacity()
        identity()
        return {profile=capacity.name,revision=capacity.revision,provider_revision=revision,
            scope='RETAINED_NATIVE_PROVIDER_REQUEST_COUNT',requests_used=count,
            requests_limit=capacity.native_history,eviction=false,future_fit_verified=false}
    end
    -- Trusted installed observers may associate an event with our own request.
    -- This is historical preparation/submission evidence, never an order handle
    -- capability or a claim that the request caused an observed event.
    function api.order_binding(request_id)
        local s=identity();assert(hex(request_id,32),"owned request identity required")
        local r=records[request_id]
        assert(r and r.request.action=="order.submit" and r.ticket and
            r.prepared and r.prepared.status=="PREPARED","owned prepared order required")
        local a,p=r.request.arguments,r.prepared
        local ship=id(resolve(a.ship,"ship"))
        local target=id(resolve(a.target,a.kind=="move" and "sector" or "object"))
        assert(ship==p.ship and target==p.target,"owned order subject changed")
        local out={request=request_id,digest=r.request.digest,provider="x4-native-v1",revision=revision,
            stamp={},kind=a.kind,policy=a.policy,ship_token=a.ship,target_token=a.target,
            ship=ship,target=target,order=p.order,prepared_status=p.status,
            dispatched=r.dispatched==true,native_status="NOT_DISPATCHED",order_id="UNKNOWN",
            wrapper_dispatch_readback_qualified=r.dispatch_readback_qualified==true,
            action_attempted="UNKNOWN",arrival_verified=false,order_caused_event_verified=false,
            scope="OWNED_ORDER_REQUEST_ASSOCIATION_ONLY"}
        for k,v in pairs(s) do out.stamp[k]=v end
        if r.dispatched then
            out.native_status="UNKNOWN"
            if type(r.executed)=="table" then
                if type(r.executed.status)=="string" then out.native_status=r.executed.status end
                if type(r.executed.action_attempted)=="boolean" then out.action_attempted=r.executed.action_attempted end
                if r.executed.order_id~=nil then out.order_id=id(r.executed.order_id) end
            end
        end
        identity()
        assert(id(resolve(a.ship,"ship"))==ship and
            id(resolve(a.target,a.kind=="move" and "sector" or "object"))==target,"issued binding changed during read")
        identity()
        return out
    end
    api.spec={id="x4-native-v1",revision=revision,actions=actions,
        session=api.session,invoke=invoke,dispose=api.dispose}
    -- Read-only data stays outside action receipts. Caller owns bounded paging/encoding.
    function api.observe(kind,token)
        identity()
        local result
        if kind=="menu" then assert(token==nil,"menu observation takes no subject");result=adapters.menu.observe()
        elseif kind=="orders" then assert(text(token),"issued ship required");result=adapters.orders.observe(token)
        elseif kind=="transactions" then assert(text(token),"issued station required");result=adapters.transactions.capture(token)
        else error("closed native observation vocabulary") end
        identity();return result
    end
    return api
end

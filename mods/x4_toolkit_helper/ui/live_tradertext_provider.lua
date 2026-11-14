-- Fixed optional SDK actions over built trader widgets and encyclopedia descriptors.
-- Hex pages follow live_automation.lua's immutable byte-page protocol, not native rereads.
return function(d)
    local function hex(v,n)return type(v)=='string' and #v==n and not v:find('[^0-9a-f]')end
    assert(type(d)=='table' and hex(d.revision,64) and type(d.current)=='function'
        and type(d.encode)=='function' and type(d.now)=='function','trader/text provider dependencies')
    local trader,text,nav,actor=d.trader,d.text,d.navigation,d.actor
    for _,k in ipairs({'observe','prepare_open','prepare_expand','prepare_select','prepare_confirm','execute','dispose'})do assert(type(trader[k])=='function','trader adapter method')end
    for _,k in ipairs({'read','dispose'})do assert(type(text[k])=='function','text adapter method')end
    for _,k in ipairs({'observe','prepare','execute','dispose','capacity'})do assert(type(nav[k])=='function','navigation adapter method')end
    for _,k in ipairs({'observe','prepare','execute','dispose','capacity'})do assert(type(actor[k])=='function','actor adapter method')end
    local profile=d.capacity_profile
    if profile==nil then profile='interactive' end
    assert(profile=='interactive' or profile=='campaign_v1','unknown capacity profile')
    local limit=256
    local capacity_revision,capacity_source='UNBOUND_LEGACY_INTERACTIVE','UNBOUND_LEGACY_INTERACTIVE'
    if d.capacity_resolver~=nil then
        assert(type(d.capacity_resolver)=='function','trusted resolver required')
        local c=d.capacity_resolver(profile)
        assert(type(c)=='table' and c.name==profile and type(c.sdk_history)=='number'
            and c.sdk_history%1==0 and c.sdk_history>=1 and c.sdk_history<=8192
            and hex(c.revision,64) and hex(c.source_sha256,64),'fixed provider capacity')
        limit=c.sdk_history;capacity_revision=c.revision;capacity_source=c.source_sha256
    else assert(profile=='interactive','campaign capacity resolver required')end
    local current,encode,now,revision=d.current,d.encode,d.now,d.revision
    local api,records,count,stamp,capture,retired,failed={}, {},0,nil,nil,false,false
    local actions={['trader.observe']=true,['text.observe']=true,page=true,
        ['trader.open']=true,['trader.expand']=true,['trader.select']=true,['trader.confirm']=true}
    local writes={['trader.open']=true,['trader.expand']=true,['trader.select']=true,['trader.confirm']=true}
    local nav_actions={['text.open']='root_open',['text.open_component']='component_open',
        ['text.select']='select',['text.expand']='expand',['property.open_map']='map_open',
        ['property.open']='property_open',['view.close']='close'}
    for k in pairs(nav_actions)do actions[k]=true;writes[k]=true end
    local actor_actions={['actor.focus']='focus',['actor.comm']='comm',['actor.interact']='interact'}
    for k in pairs(actor_actions)do actions[k]=true;writes[k]=true end
    actions['actor.observe']=true
    actions['text.entries']=true;actions['property.observe']=true
    actions.capacity_observe=true
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,a,b)return finite(v) and v%1==0 and v>=a and v<=b end
    local function token(v)return type(v)=='string' and #v>0 and #v<=256 and not v:find('[%c]')end
    local function copy(v)if type(v)~='table'then return v end;local t={};for k,x in pairs(v)do t[k]=copy(x)end;return t end
    local function identity()
        assert(not retired,'provider retired');local s=current()
        assert(type(s)=='table' and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=4096,'public identity unavailable')
        local out={};for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            if stamp then assert(s[k]==stamp[k],'public identity changed')end;out[k]=s[k]
        end
        stamp=out;local t=now();assert(finite(t) and t>=0,'UI clock unavailable');return s.session,t
    end
    local function arguments(req)
        local a=req.arguments;assert(type(a)=='table' and getmetatable(a)==nil,'flat arguments required')
        local fields
        if req.action=='page'then fields={capture=true,index=true}
        elseif req.action=='capacity_observe'then fields={}
        elseif req.action=='text.entries' or req.action=='property.observe' or req.action=='actor.observe'then fields={start=true}
        elseif actor_actions[req.action]then fields={actor=true}
        elseif nav_actions[req.action]then
            fields=req.action=='view.close' and {domain=true} or req.action=='text.open_component' and {subject=true}
                or (req.action=='text.select' or req.action=='text.expand') and {row=true} or {}
        elseif req.action=='text.observe'then fields={section=true,start=true}
        elseif req.action=='trader.observe'then fields={trader=true,mode=true,start=true}
        elseif req.action=='trader.open'then fields={trader=true,mode=true}
        elseif req.action=='trader.expand'then fields={trader=true,mode=true,item=true,expanded=true}
        else fields={trader=true,mode=true,item=true,price=true}end
        for k in pairs(a)do assert(fields[k],'extra argument')end
        for k in pairs(fields)do assert(a[k]~=nil,'missing argument')end
        if req.action=='page'then assert(hex(a.capture,32) and integer(a.index,0,59),'page identity/index')
        elseif req.action=='capacity_observe'then -- closed empty args; no native preparation
        elseif req.action=='actor.observe'then assert(integer(a.start,1,129),'actor window')
        elseif actor_actions[req.action]then assert(token(a.actor),'observed actor token')
        elseif req.action=='text.entries' or req.action=='property.observe'then assert(integer(a.start,1,4097),'model window')
        elseif nav_actions[req.action]then
            if a.domain~=nil then assert(a.domain=='encyclopedia' or a.domain=='property','closed view domain')end
            if a.subject~=nil then assert(token(a.subject),'issued component')end
            if a.row~=nil then assert(token(a.row),'observed row token')end
        elseif req.action=='text.observe'then assert((a.section=='index' or a.section=='description' or a.section=='detail') and integer(a.start,1,4097),'text section/window')
        else
            assert(token(a.trader) and (a.mode=='licence' or a.mode=='blueprint'),'issued trader/mode')
            if req.action=='trader.observe'then assert(integer(a.start,1,513),'trader window')
            elseif req.action=='trader.open'then assert(a.mode=='licence','licence opener only')
            else assert(token(a.item),'exact item/category');if req.action=='trader.expand'then assert(type(a.expanded)=='boolean','desired expanded state')
                else assert(finite(a.price) and a.price>0 and a.price<90071992547409,'exact positive quote')end end
        end
        return copy(a)
    end
    local function reply(status,reason,value,attempted)
        if type(attempted)~='boolean'then attempted='UNKNOWN'end
        local out={status=status,reason=reason,observations={scope='FIXED_NATIVE_TRADER_TEXT_OR_ACTOR_ACTION',
            action_attempted=attempted,purchase_verified=false,rendered_pixels_verified=false,runtime_verified=false}}
        if value then out.observations.payload_json=encode(value)end
        assert(#encode({payload=encode(out),reserve=string.rep('x',900)})<=4000,'wrapped action receipt budget')
        return out
    end
    local function header(c)
        return {kind='IMMUTABLE_WINDOW_CAPTURE',capture=c.id,source_action=c.action,source_status=c.status,
            bytes=#c.payload,pages=math.max(1,math.ceil(#c.payload/500)),chunk_bytes=500,
            captured_ui_age=c.age,atomic=false,full_catalogue=false,purchase_verified=false,rendered_pixels_verified=false}
    end
    local function invoke(phase,req,done)
        assert(type(done)=='function','SDK callback required');local r=type(req)=='table' and records[req.id] or nil
        local ok,out=pcall(function()
            local session,age=identity()
            assert(type(req)=='table' and req.version==1 and req.provider=='x4-trader-text-v1' and actions[req.action]
                and req.binding==revision and req.session==session and hex(req.id,32) and hex(req.digest,64),'unbound request')
            local args=arguments(req)
            if r then
                assert(r.action==req.action and r.digest==req.digest and r.session==req.session,'request changed')
                for k,v in pairs(r.args)do assert(args[k]==v,'prepared argument changed')end
            end
            if phase=='prepare'then
                assert(not r and count<limit,'duplicate/capacity')
                r={action=req.action,args=args,session=session,digest=req.digest,sent=false};records[req.id]=r;count=count+1
                if writes[req.action]then
                    local v
                    if actor_actions[req.action]then v=actor.prepare(actor_actions[req.action],args.actor)
                    elseif nav_actions[req.action]then v=nav.prepare(nav_actions[req.action],args.subject or args.row or args.domain)
                    elseif req.action=='trader.open'then v=trader.prepare_open(args.trader,args.mode)
                    elseif req.action=='trader.expand'then v=trader.prepare_expand(args.trader,args.mode,args.item,args.expanded)
                    elseif req.action=='trader.select'then v=trader.prepare_select(args.trader,args.mode,args.item,args.price)
                    else v=trader.prepare_confirm(args.trader,args.mode,args.item,args.price)end
                    identity();assert(type(v)=='table','native preparation missing')
                    if v.status~='PREPARED'then return reply('refused','native trader preparation refused',nil,false)end
                    assert(type(v.ticket)=='string','private native ticket missing');r.ticket=v.ticket
                elseif req.action=='page'then
                    assert(capture and capture.id==args.capture and args.index<math.max(1,math.ceil(#capture.payload/500)),'capture absent/superseded/index')
                end
                r.ready=true;return reply('prepared','fixed private preparation; no button dispatched',nil,false)
            end
            assert(r and r.ready,'no qualified private preparation')
            if phase=='status'then
                if not r.sent then return reply('prepared','preparation retained; execute rechecks native state',nil,false)end
                assert(r.result,'dispatch/read outcome unavailable');return copy(r.result)
            end
            assert(phase=='execute' and not r.sent,'unsupported phase or already consumed');r.sent=true
            if writes[r.action]then
                r.possible=true;local v=actor_actions[r.action] and actor.execute(r.ticket) or nav_actions[r.action] and nav.execute(r.ticket) or trader.execute(r.ticket);identity()
                assert(type(v)=='table' and (actor_actions[r.action] and v.conversation_verified==false or nav_actions[r.action] and v.selection_verified==false or v.purchase_verified==false
                    or v.status=='REFUSED' and v.action_attempted==false),'native result scope unavailable')
                local state=v.status=='DISPATCHED_UNVERIFIED' and 'running' or v.status=='REFUSED' and v.action_attempted==false and 'refused' or 'unknown'
                -- Do not transport unbounded native reason text after a possible economic dispatch.
                local value={native_status=v.status,item=r.args.item,mode=r.args.mode,action=r.action,
                    actor=v.actor,native_result=v.native_result,conversation_verified=false}
                -- An open is confirmed only by a later trader.observe that finds the menu shown.
                if r.action=='trader.open'then value.menu_open_verified=false end
                r.result=reply(state,'native wrapper outcome only; purchase/setting effect unverified',value,v.action_attempted)
            elseif r.action=='capacity_observe'then
                local v={scope='LOCAL_COUNTERS_AT_EXECUTE_RETAINED_BY_SDK',profile=profile,
                    revision=capacity_revision,source_sha256=capacity_source,
                    provider_history_used=count,provider_history_limit=limit,navigation=nav.capacity(),actor=actor.capacity(),
                    includes_this_provider_request=true,includes_subsequent_requests=false,
                    current_counts_verified=false,native_operation_records_allocated=0}
                r.result=reply('completed','local counters only; terminal status retains this sample',v,false)
            elseif r.action=='page'then
                assert(capture and capture.id==args.capture and age>=capture.age and age-capture.age<=300,'capture absent/superseded/expired')
                local h=header(capture);assert(args.index<h.pages,'page outside capture')
                local bytes=capture.payload:sub(args.index*500+1,args.index*500+500)
                h.kind='IMMUTABLE_WINDOW_PAGE';h.index=args.index;h.offset=args.index*500;h.length=#bytes
                h.hex=bytes:gsub('.',function(c)return string.format('%02x',c:byte())end)
                r.result=reply('completed','retained bytes only; source status remains explicit',h,false)
            else
                capture=nil
                local v
                if r.action=='actor.observe'then v=actor.observe(args.start)
                elseif r.action=='trader.observe'then v=trader.observe(args.trader,args.mode,args.start)
                elseif r.action=='text.entries'then v=nav.observe('encyclopedia',args.start)
                elseif r.action=='property.observe'then v=nav.observe('property',args.start)
                else v=text.read(args.section,args.start)end
                local _,ended=identity();assert(ended>=age and type(v)=='table' and type(v.status)=='string','observation unavailable')
                local payload=encode(v);assert(type(payload)=='string' and #payload<=30000,'captured observation budget')
                capture={id=req.id,action=r.action,status=v.status,payload=payload,age=ended}
                r.result=reply(v.status=='OBSERVED' and 'completed' or 'unknown','captured scoped window; retrieve immutable pages',header(capture),false)
            end
            return copy(r.result)
        end)
        if not ok then out=reply(r and 'unknown' or 'refused',tostring(out):sub(1,256),nil,r and r.possible or false)end
        done(out)
    end
    function api.session()local ok,s=pcall(identity);return ok and s or nil end
    function api.dispose()
        if failed then return false end;if retired then return true end;retired=true
        local good=true;for _,a in ipairs({trader,text,nav,actor})do local ok,v=pcall(a.dispose);if not ok or v~=true then good=false end end
        if not good then failed=true end;return good
    end
    api.spec={id='x4-trader-text-v1',revision=revision,actions=actions,session=api.session,invoke=invoke,dispose=api.dispose}
    return api
end

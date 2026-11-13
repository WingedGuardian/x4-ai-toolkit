-- UI-only assembly. Never reads a player/blackboard or constructs an MD session.
local singleton
local function new(d)
    assert(not singleton,"previous start bootstrap must retire")
    assert(type(d)=="table" and type(d.modules)=="table","UI modules required")
    local function hex(s,n) return type(s)=="string" and #s==n and not s:find("[^0-9a-f]") end
    assert(hex(d.build,64),"source-bound bundle build required")
    local BUILD=d.build
    local g,C,H,pipes=d.globals,d.C,d.Helper,d.pipes
    assert(type(g)=="table" and C and type(H)=="table" and type(pipes)=="table","native UI dependencies required")
    local api,tickets,pending,used={},{},{},{}
    local generation,process_binding,profile,latest=nil,nil,nil,nil
    local count,authcount=0,0
    local disposed,disposal_failed,last=false,false,nil
    local menu,pump
    local function live()
        assert(not disposed,"start bootstrap retired")
        assert(C.IsStartmenu()==true,"not native start menu")
        local t=g.getElapsedTime()
        assert(type(t)=="number" and t==t and t>=0 and t<=9007199254740991 and
            (not last or t>=last),"invalid/backward UI age")
        last=t;return t
    end
    local function fields(a,keys)
        assert(type(a)=="table","UI argument object required")
        local n=0;for k in pairs(a) do assert(keys[k],"unexpected bootstrap argument");n=n+1 end
        local want=0;for k in pairs(keys) do assert(a[k]~=nil,"missing bootstrap argument");want=want+1 end
        assert(n==want,"bootstrap argument count")
    end
    function api.identity()
        live()
        return {build=BUILD,generation=generation or "",process_binding=process_binding or "",
            profile_id=profile or "",scope="UI_ONLY_NO_WORLD"}
    end
    function api.binding() live();return generation or "unbound" end
    function api.handle(action,a,call)
        local age=live()
        assert(hex(call,32),"UI call identity required")
        if action=="ui_state" then
            fields(a,{challenge=true});assert(hex(a.challenge,32) and not tickets[a.challenge] and count<256,"fresh UI challenge required")
            count=count+1;latest=a.challenge
            tickets[latest]={generation=generation,age=age,used=false}
            return {kind="UI_STATE",challenge=latest,generation=generation or "",build=BUILD}
        elseif action=="ui_bind" then
            fields(a,{proof=true,generation=true,process_binding=true,profile_id=true})
            local ticket=tickets[a.proof]
            assert(hex(a.proof,32) and a.proof==latest and ticket and not ticket.used and
                age-ticket.age<=10,"unknown/consumed/stale UI proof")
            assert(hex(a.generation,32) and hex(a.process_binding,64) and type(a.profile_id)=="string"
                and #a.profile_id>=1 and #a.profile_id<=20 and not a.profile_id:find("[^0-9]"),"malformed UI scope")
            ticket.used=true
            if generation then
                assert(ticket.generation==generation and a.generation==generation and
                    a.process_binding==process_binding and a.profile_id==profile,"UI scope rebound")
            else
                assert(ticket.generation==nil,"stale unbound proof")
                generation=a.generation;process_binding=a.process_binding;profile=a.profile_id
            end
            return {kind="UI_BOUND",challenge=a.proof,generation=generation,build=BUILD,
                process_binding=process_binding,profile_id=profile}
        elseif action=="authorize" then
            fields(a,{request_id=true,gamestart=true,capability=true,context_sha256=true})
            assert(generation and hex(a.request_id,32) and hex(a.capability,32) and
                hex(a.context_sha256,64) and not used[a.capability] and authcount<128,"invalid/replayed authorization")
            local r=pending[a.request_id]
            assert(r and not r.consumed and r.context.binding==generation and r.context.gamestart==a.gamestart
                and age-r.age<=30,"no matching pending Start")
            -- Only a qualified connected host dispatcher may send this operation.
            -- The nonce/hash correlates its policy evidence; it is not native human attestation.
            r.consumed=true;used[a.capability]=true;authcount=authcount+1
            r.callback(true,"AGENT_RECORDED_CONVERSATION")
            return {kind="START_AUTHORIZATION_CONSUMED",request_id=a.request_id,
                capability=a.capability,context_sha256=a.context_sha256,result=menu.status(a.request_id)}
        end
        error("unsupported bootstrap operation")
    end
    local function authorize(context,callback)
        live()
        assert(generation and context.scope=="UI_ONLY_NO_WORLD" and context.binding==generation and
            hex(context.request_id,32) and type(context.gamestart)=="string" and
            type(callback)=="function" and not pending[context.request_id],"unqualified Start preparation")
        pending[context.request_id]={context=context,callback=callback,age=last,consumed=false}
    end
    function api.dispose()
        if disposal_failed then return false end
        if disposed then return true end
        disposed=true;tickets={};latest=nil
        local ok,value=true,true
        if pump then ok,value=pcall(pump.dispose) elseif menu then ok,value=pcall(menu.dispose) end
        if not ok or value==false then disposal_failed=true;return false end
        pending={};used={}
        if singleton==api then singleton=nil end
        return true
    end
    local ok,err=pcall(function()
        live()
        menu=d.modules.menu.native(g,C,H,api.binding,authorize)
        local codec=d.modules.codec()
        pump=d.modules.pipe.new({read_pipe=pipes.Read_Pipe,write_pipe=pipes.Write_Pipe,
            schedule=H.addDelayedOneTimeCallbackOnUpdate,now=g.getElapsedTime,
            is_startmenu=function() return C.IsStartmenu() end,ui_binding=api.binding,
            codec=codec,adapter=menu,bootstrap=api})
    end)
    if not ok then
        local retired=api.dispose()
        error("start bootstrap failed: "..tostring(err)..(retired and "" or "; retirement failed"))
    end
    function api.start() live();return pump.start() end
    function api.status() return pump.status() end
    singleton=api
    return api
end
return {new=new}

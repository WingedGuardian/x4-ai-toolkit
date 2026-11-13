-- Dedicated start-menu pipe pump; no MSA shared Init/Time/onUpdate changes.
-- Wire session field is a UI instance token ONLY, never an MD world session.
local owner
local PIPE="x4live_startmenu"
local function new(d)
    for _,k in ipairs({"read_pipe","write_pipe","schedule","now","is_startmenu","ui_binding"}) do
        assert(type(d[k])=="function","missing UI pipe dependency: "..k)
    end
    assert(type(d.codec)=="table" and type(d.codec.decode)=="function" and
        type(d.codec.encode)=="function" and type(d.adapter)=="table","UI pipe adapters required")
    for _,k in ipairs({"observe","prepare","execute","status","dispose"}) do
        assert(type(d.adapter[k])=="function","missing native start adapter")
    end
    assert(not owner,"prior UI pump must be disposed")
    local api,cache={},{}
    local count,last,started,binding,scheduled,scheduling,faulted,disposed=0,nil,nil,nil,false,false,nil,false
    local retirement_failed=false
    local function finite(t) return type(t)=="number" and t==t and t>=0 and t<=9007199254740991 end
    local function token(s) return type(s)=="string" and #s>=1 and #s<=128 and not s:find("[^A-Za-z0-9_.:-]") end
    local function hex(s,n) return type(s)=="string" and #s==n and not s:find("[^0-9a-f]") end
    local function clock()
        local t=d.now();assert(finite(t) and (not last or t>=last),"invalid/backward UI clock");last=t;return t
    end
    local function fault(reason)
        faulted=tostring(reason)
        local ok,value=pcall(d.adapter.dispose)
        if not ok or value==false then retirement_failed=true end
    end
    local function current()
        assert(not disposed and not faulted,"UI pump unavailable")
        assert(d.is_startmenu()==true,"left native start menu")
        assert(d.ui_binding()==binding,"UI instance changed")
        local t=clock();assert(t-started<=1800,"UI pump lifetime expired");return t
    end
    local function fields(args,wanted)
        assert(type(args)=="table","arguments absent")
        for k in pairs(args) do assert(wanted[k]~=nil,"unexpected UI argument") end
        for k,required in pairs(wanted) do assert(not required or args[k]~=nil,"missing UI argument") end
    end
    local function dispatch(r)
        local a=r.arguments
        if r.action=="observe" then fields(a,{});return d.adapter.observe() end
        assert(r.action=="prepare" or r.action=="execute" or r.action=="status","unknown UI operation")
        if r.action=="prepare" then
            fields(a,{request_id=true,action=true,option=false})
            assert(hex(a.request_id,32) and (a.action=="open_new" or a.action=="select" or a.action=="start"),"invalid preparation")
            if a.action=="open_new" then assert(a.option==nil,"unexpected start ID")
            else assert(type(a.option)=="string" and #a.option>0 and #a.option<=256,"invalid start ID") end
            return d.adapter.prepare(a.request_id,a.action,a.option)
        end
        fields(a,{request_id=true});assert(hex(a.request_id,32),"invalid request identity")
        if r.action=="execute" then return d.adapter.execute(a.request_id) end
        return d.adapter.status(a.request_id)
    end
    local function envelope(r,value)
        local out={version=1,scope="UI_ONLY_NO_WORLD",provider="x4-ui-startmenu-v1",
            call_id=r.id,ui_instance=binding,world_session=false,result=value}
        if d.bootstrap then
            local identity=d.bootstrap.identity()
            out.build=identity.build;out.ui_generation=identity.generation
            out.process_binding=identity.process_binding;out.profile_id=identity.profile_id
        end
        return out
    end
    local function handle(raw)
        assert(type(raw)=="string" and #raw<=1400,"bounded complete UI packet required")
        local r=d.codec.decode(raw)
        assert(r.version==1 and r.provider=="x4-ui-startmenu-v1","wrong UI namespace")
        local handshake=r.action=="ui_state" or r.action=="ui_bind"
        if handshake then
            assert(d.bootstrap and r.session=="bootstrap" and r.binding=="bootstrap","invalid UI bootstrap request")
        else
            assert(r.session==binding and r.binding==binding and (not d.bootstrap or binding~="unbound"),
                "wrong or unbound UI instance")
        end
        assert(hex(r.id,32) and hex(r.digest,64),"invalid UI call identity")
        if cache[r.id] then
            assert(cache[r.id].raw==raw,"changed packet under prior call ID")
            return cache[r.id].reply
        end
        assert(count<128,"UI packet cache full")
        -- Consume complete raw bytes before dispatch. A digest is caller attribution,
        -- not independently computed native SHA or authenticated authorization.
        count=count+1;local record={raw=raw};cache[r.id]=record
        local ok,value=pcall(function()
            if handshake or r.action=="authorize" then
                assert(d.bootstrap,"UI authority unavailable")
                local value=d.bootstrap.handle(r.action,r.arguments,r.id)
                if r.action=="ui_bind" then
                    local newbinding=d.ui_binding();assert(hex(newbinding,32),"UI bind did not establish generation")
                    binding=newbinding
                end
                return value
            end
            return dispatch(r)
        end)
        if not ok then value={status="UNKNOWN",reason=tostring(value),automatic_retry=false} end
        local good,encoded=pcall(d.codec.encode,envelope(r,value))
        if not good or #encoded>4096 then
            encoded=d.codec.encode(envelope(r,{status="UNAVAILABLE",reason="response exceeds wire bounds",
                complete=false,action_outcome=value.status or "UNKNOWN",automatic_retry=false}))
        end
        assert(type(encoded)=="string" and #encoded<=4096,"UI response cannot be bounded")
        record.reply=encoded
        return encoded
    end
    local pump,arm
    arm=function()
        assert(not scheduled,"duplicate UI schedule")
        local t=current();scheduled=true;scheduling=true
        local ok,err=pcall(d.schedule,function()
            if disposed or faulted then return end
            if scheduling then fault("synchronous delayed callback refused");return end
            scheduled=false;pump()
        end,false,t+0.1)
        scheduling=false
        if not ok then scheduled=false;fault(err) end
    end
    pump=function()
        local ok,err=pcall(function()
            current()
            local success,raw=d.read_pipe(PIPE)
            assert(success==true,"native UI pipe read failed")
            if raw~=nil then
                local reply=handle(raw)
                current() -- No response attribution after lifecycle drift.
                assert(d.write_pipe(PIPE,reply)==true,"native UI pipe write uncertain")
            end
            current();arm()
        end)
        if not ok then fault(err) end
    end
    function api.start()
        assert(owner==api and not started and not disposed and not faulted,"UI pump already started/unavailable")
        assert(d.is_startmenu()==true,"not native start menu")
        binding=d.ui_binding();assert(token(binding),"UI instance token unavailable")
        started=clock();arm()
        return {status=faulted and "UNAVAILABLE" or "SCHEDULED",scope="UI_ONLY_NO_WORLD",pipe=PIPE,
            ui_instance=binding,world_session=false}
    end
    function api.status()
        return {status=disposed and "DISPOSED" or (faulted and "UNAVAILABLE" or (started and "SCHEDULED" or "IDLE")),
            reason=faulted or "",scope="UI_ONLY_NO_WORLD",calls=count,scheduled=scheduled,world_session=false}
    end
    function api.dispose()
        disposed=true;scheduled=false
        if retirement_failed then return false end
        local ok,value=pcall(d.adapter.dispose)
        if not ok or value==false then retirement_failed=true;faulted="native adapter disposal failed";return false end
        if owner==api then owner=nil end
        return true
    end
    owner=api
    return api
end
local function native(pipes,Helper,g,C,codec,adapter,ui_binding)
    assert(type(pipes)=="table" and type(Helper)=="table","native pipe dependencies unavailable")
    return new({read_pipe=pipes.Read_Pipe,write_pipe=pipes.Write_Pipe,
        schedule=Helper.addDelayedOneTimeCallbackOnUpdate,now=g.getElapsedTime,
        is_startmenu=function() return C.IsStartmenu() end,codec=codec,adapter=adapter,ui_binding=ui_binding})
end
return {new=new,native=native,pipe=PIPE}

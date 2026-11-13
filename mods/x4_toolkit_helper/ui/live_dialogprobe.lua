-- Read-only feasibility/model probe; no option selection or conversation action.
-- Native evidence: ui/core/lua/dialogmenu.lua:188-224 (local handlers),
-- 626-634 (shown/selectable gate), 746-752 (select+hide), 850-858 (five results),
-- 1124 (IsDialogActive); ui/addons/ego_detailmonitorhelper/helper.lua:400-401.
return function(d)
    assert(type(d)=='table' and type(d.globals)=='table' and d.ffi and d.C
        and type(d.public_current)=='function' and type(d.clock)=='function','dialog dependencies required')
    d.ffi.cdef[[bool IsConversationActive(void); bool IsConversationCancelling(void);]]
    local globals,C,current,clock=d.globals,d.C,d.public_current,d.clock
    local closed=false
    local names={'IsDialogActive','GetDialogOption','SelectDialogOption','clickDialogButton',
        'leaveDialogMenu','onDialog1MouseClick','ProceedFromMenu','GetInputActionMap'}
    local function finite(v) return type(v)=='number' and v==v and v>=0 and v<math.huge end
    local function hex(v,n) return type(v)=='string' and #v==n and not v:find('[^0-9a-f]') end
    local function stamp()
        local s=current()
        assert(type(s)=='table' and getmetatable(s)==nil,'public stamp unavailable')
        local allowed={provider=true,revision=true,session=true,generation=true,world_token=true}
        local n=0;for key in pairs(s) do assert(allowed[key],'extra public stamp field');n=n+1 end
        assert(n==5 and s.provider=='x4-live-session-v1' and hex(s.revision,64)
            and hex(s.session,32) and hex(s.generation,32) and type(s.world_token)=='string'
            and #s.world_token>0 and #s.world_token<=4096,'invalid public stamp')
        local out={};for k,v in pairs(s) do out[k]=v end;return out
    end
    local function equal(a,b)
        for k,v in pairs(a) do if b[k]~=v then return false end end
        for k,v in pairs(b) do if a[k]~=v then return false end end;return true
    end
    local function bindings(fn)
        -- gameoptions:4844 current map;4684 action labels use page1005.
        -- Native page1005 IDs97..102 are dialogue choices1..6. This is not
        -- a host scancode translation or proof of a rendered selectable choice.
        assert(type(fn)=='function','current input map unavailable')
        local map=fn()
        assert(type(map)=='table' and getmetatable(map)==nil,'current input map unreadable')
        local rows={}
        local function integer(v,lo,hi)
            return finite(v) and v%1==0 and v>=lo and v<=hi
        end
        for choice=1,6 do
            local action=96+choice
            local inputs=rawget(map,action)
            local row={choice=choice,action=action,status='UNAVAILABLE',inputs={}}
            rows[#rows+1]=row
            if inputs~=nil then
                assert(type(inputs)=='table' and getmetatable(inputs)==nil,'action bindings unreadable')
                local n=0
                for k in pairs(inputs) do
                    assert(integer(k,1,4),'binding index or capacity')
                    n=n+1;assert(n<=4,'binding capacity')
                end
                for i=1,n do
                    local entry=rawget(inputs,i)
                    assert(type(entry)=='table' and getmetatable(entry)==nil,'sparse or unreadable binding')
                    for k in pairs(entry) do assert(integer(k,1,4),'unknown binding field') end
                    local source,code,sign,toggle=rawget(entry,1),rawget(entry,2),rawget(entry,3),rawget(entry,4)
                    assert(integer(source,0,65535) and integer(code,0,2147483647)
                        and type(sign)=='number' and (sign==-1 or sign==0 or sign==1),'binding values unreadable')
                    assert(toggle==nil or type(toggle)=='boolean','binding toggle unreadable')
                    -- Compact native tuple keeps the combined read inside the
                    -- existing encoder budget; absent is distinct from false.
                    row.inputs[i]={source,code,sign,toggle==nil and 'NOT_PRESENT' or toggle}
                end
                row.status='OBSERVED';row.count=n
            end
        end
        return rows
    end
    local function same_bindings(a,b)
        for i=1,6 do
            local x,y=a[i],b[i]
            if x.status~=y.status or x.count~=y.count then return false end
            for j=1,x.count or 0 do if not equal(x.inputs[j],y.inputs[j]) then return false end end
        end
        return true
    end
    local api={}
    function api.capture()
        assert(not closed,'dialog probe retired')
        local out={status='UNAVAILABLE',scope='READ_ONLY_UNBOUND_DIALOG_MODEL',atomic=false,
            actor='UNKNOWN',section='UNKNOWN',rendered_visibility='UNKNOWN',route_qualified=false,
            option_limit=6,options={},rows_read=0,visibility={},issues={},dialog_active='UNKNOWN',
            conversation_active='UNKNOWN',conversation_cancelling='UNKNOWN',
            current_bindings={status='UNAVAILABLE',scope='ACTIONS_97_TO_102_ONLY',action_count=6,
                input_fields={'source','code','signum','toggle'},
                binding_limit_per_action=4,host_key_translation='UNKNOWN',atomic=false}}
        local symbols={}
        for _,name in ipairs(names) do
            local value=rawget(globals,name);symbols[name]=value
            out.visibility[name]={lua_type=type(value),callable=type(value)=='function',
                identity_attested=false}
        end
        local function issue(value) out.issues[#out.issues+1]=value end
        local function boolean(fn,label)
            if type(fn)~='function' and type(fn)~='cdata' then issue(label..':UNAVAILABLE');return 'UNKNOWN' end
            local good,value=pcall(fn)
            if not good or type(value)~='boolean' then issue(label..':UNREADABLE');return 'UNKNOWN' end
            return value
        end
        local function cboolean(name)
            local good,fn=pcall(function() return C[name] end)
            if not good then issue(name..':UNAVAILABLE');return 'UNKNOWN' end
            return boolean(fn,name)
        end
        local good,why=pcall(function()
            out.stamp=stamp();out.started_at=clock();assert(finite(out.started_at),'invalid initial clock')
            out.conversation_active=cboolean('IsConversationActive')
            out.conversation_cancelling=cboolean('IsConversationCancelling')
            out.dialog_active=boolean(symbols.IsDialogActive,'IsDialogActive')
            local first_ok,first=pcall(bindings,symbols.GetInputActionMap)
            if first_ok then out.current_bindings.rows=first
            else out.current_bindings.reason=tostring(first):sub(1,256) end
            if out.dialog_active==true and type(symbols.GetDialogOption)=='function' then
                for i=1,6 do
                    -- Each row is separate native evidence; not an atomic rendered-menu snapshot.
                    assert(not closed,'dialog probe retired during read')
                    assert(equal(out.stamp,stamp()),'public stamp changed before option')
                    assert(boolean(symbols.IsDialogActive,'IsDialogActive_row')==true,'dialog no longer active')
                    local r={index=i,text='UNKNOWN',selectable='UNKNOWN',immediate='UNKNOWN',
                        shortcut='UNKNOWN',mouse_over='UNKNOWN',active='UNKNOWN',readable=false}
                    local okay,text,selectable,immediate,shortcut,mouse=pcall(symbols.GetDialogOption,i)
                    local valid=okay
                    local function str(value,label,limit)
                        if type(value)=='string' and #value<=limit then return value end
                        valid=false;issue('OPTION_'..i..':'..label..'_UNREADABLE');return 'UNKNOWN'
                    end
                    local function flag(value,label)
                        if type(value)=='boolean' then return value end
                        valid=false;issue('OPTION_'..i..':'..label..'_UNREADABLE');return 'UNKNOWN'
                    end
                    if okay then
                        r.text=str(text,'TEXT',1024);r.selectable=flag(selectable,'SELECTABLE')
                        r.immediate=flag(immediate,'IMMEDIATE');r.shortcut=str(shortcut,'SHORTCUT',128)
                        r.mouse_over=str(mouse,'MOUSE_OVER',1024)
                        if type(text)=='string' and #text<=1024 then r.active=text~='' end
                    else issue('OPTION_'..i..':CALL_FAILED') end
                    r.readable=valid;out.options[#out.options+1]=r;out.rows_read=i
                end
            elseif out.dialog_active==true then issue('GetDialogOption:UNAVAILABLE') end
            assert(not closed,'dialog probe retired during read')
            if first_ok then
                assert(equal(out.stamp,stamp()),'public stamp changed before input recheck')
                local second_ok,second=pcall(bindings,symbols.GetInputActionMap)
                assert(not closed,'dialog probe retired during input recheck')
                if second_ok and same_bindings(first,second) then
                    out.current_bindings.status='TWO_EQUAL_PROJECTED_READS'
                else
                    out.current_bindings.status='UNQUALIFIED'
                    out.current_bindings.reason=second_ok and 'current bindings changed'
                        or tostring(second):sub(1,256)
                end
            end
            out.dialog_active_after=boolean(symbols.IsDialogActive,'IsDialogActive_after')
            assert(out.dialog_active==out.dialog_active_after,'dialog activation changed during read')
            assert(equal(out.stamp,stamp()),'public stamp changed after options')
            out.finished_at=clock()
            assert(finite(out.finished_at) and out.finished_at>=out.started_at,'invalid final clock')
            for _,name in ipairs(names) do assert(rawget(globals,name)==symbols[name],'global changed: '..name) end
            if out.dialog_active==false then out.status='INACTIVE_OBSERVED'
            elseif out.dialog_active==true and out.rows_read==6 then
                out.status=#out.issues==0 and 'UNBOUND_MODEL_OBSERVED' or 'PARTIAL_UNBOUND_MODEL'
            end
        end)
        if not good then
            out.status='UNQUALIFIED';issue(tostring(why):sub(1,512))
            if out.current_bindings.rows then out.current_bindings.status='UNQUALIFIED' end
        end
        return out
    end
    function api.dispose() closed=true;return true end
    return api
end

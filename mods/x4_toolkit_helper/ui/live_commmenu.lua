-- Actual built MapMenu info_actor_comm wrapper, not StartConversationFromMenu.
-- Native map26123-26244 binds context/entity/person and installs the rendered row;
-- helper4894-4901 preserves rowdata. Widget/wrapper gates mirror live_menu.lua.
return function(d)
    assert(type(d)=="table","native menu dependencies required")
    for _,k in ipairs({"menus","public_current","clock","resolve_issued","component_id","cell_id","widget_state"}) do
        assert(type(d[k])=="function","missing comm-menu dependency: "..k)
    end
    local api,records,serial,retired={}, {},0,false
    local function finite(v) return type(v)=="number" and v==v and v>=0 and v<math.huge end
    local function wid(v) return finite(v) and v>=1 and v<=2147483647 and v%1==0 end
    local function id(v)
        if type(v)=="cdata" then v=tostring(v):match("^(%d+)ULL$") end
        if type(v)=="number" then
            assert(finite(v) and v%1==0 and v<9007199254740992,"inexact native identity")
            v=string.format("%.0f",v)
        end
        assert(type(v)=="string" and v:match("^[1-9]%d*$") and #v<=20
            and (#v<20 or v<="18446744073709551615"),"exact positive native identity required")
        return v
    end
    local function seq(t,limit)
        assert(type(t)=="table" and #t<=limit,"bounded native list required")
        local n=0;for k in pairs(t) do assert(type(k)=="number" and k%1==0 and k>=1 and k<=#t,"sparse native list");n=n+1 end
        assert(n==#t,"sparse native list");return t
    end
    local function stamp()
        assert(not retired,"comm-menu retired")
        local s=d.public_current();local copy={}
        assert(type(s)=="table" and s.provider=="x4-live-session-v1","public session unavailable")
        for _,k in ipairs({"provider","revision","session","generation","world_token"}) do
            assert(type(s[k])=="string" and #s[k]>0,"incomplete public identity");copy[k]=s[k]
        end
        local age=d.clock();assert(finite(age),"native clock unavailable")
        return copy,age
    end
    local function same(a,b) for k,v in pairs(a) do if b[k]~=v then return false end end;return true end
    local function copy(t) local out={};for k,v in pairs(t) do out[k]=v end;return out end
    local function context(m)
        local c=m.contextMenuData
        assert(type(c)=="table" and c.component~=nil,"actor context unavailable")
        local component=id(d.component_id(c.component))
        local entity=(c.entity~=nil and c.entity~=false) and id(d.component_id(c.entity)) or "UNKNOWN"
        -- Native person is not a component; preserve exact observed token without conversion.
        local person=(c.person~=nil and c.person~=false) and id(c.person) or "UNKNOWN"
        assert(person~="UNKNOWN" or entity~="UNKNOWN","no actor in context")
        for _,k in ipairs({"inv_ware","weaponmacro","equipmentmacro","software"}) do assert(not c[k],"mixed non-actor context") end
        return {component=component,entity=entity,person=person,kind=person~="UNKNOWN" and "PERSON" or "ENTITY"},c,c.instance
    end
    local function current()
        local found
        for _,m in ipairs(seq(d.menus(),256)) do
            assert(type(m)=="table","invalid registered menu")
            if m.name=="MapMenu" then assert(not found,"duplicate MapMenu");found=m
            else assert(not m.shown,"another menu is open") end
        end
        local m=assert(found,"MapMenu unavailable")
        assert(m.shown==true and m.minimized==false and m.contextMenuMode=="info_context","actor menu is not visible")
        local f=m.contextFrame
        assert(type(f)=="table" and wid(f.id) and type(f.properties)=="table" and f.properties.layer==2
            and type(m.frames)=="table" and m.frames[2]==f.id,"context frame not built/registered")
        local model,c,instance=context(m)
        local found_widget
        for _,t in ipairs(seq(f.content,64)) do
            if t.type=="table" then
                assert(wid(t.id),"context table not built")
                for ri,row in ipairs(seq(t.rows,512)) do
                    if row.rowdata=="info_actor_comm" then
                        assert(not found_widget and row.index==ri and #row==1,"ambiguous actor communication row")
                        local cell=row[1]
                        assert(type(cell)=="table" and cell.type=="button" and cell.colspan~=0
                            and wid(cell.id) and d.cell_id(t.id,ri,1)==cell.id,"stale communication widget")
                        local p,h=cell.properties,cell.handlers
                        assert(type(p)=="table" and type(p.active)=="boolean" and type(h)=="table"
                            and type(h.onClick)=="function","native button descriptor unavailable")
                        local state=d.widget_state(cell.id)
                        assert(type(state)=="table" and type(state.active)=="boolean" and state.active==p.active,
                            "native/descriptor active mismatch")
                        local wrapper
                        for _,w in ipairs(seq(m.buttonScriptMap,4096)) do
                            if w.layer==2 and w.tableobj==t.id and w.row==ri and w.col==1 and w.type=="onClick" then
                                assert(not wrapper and w.button==cell.id and type(w.script)=="function","ambiguous native wrapper")
                                wrapper=w.script
                            end
                        end
                        assert(wrapper,"native built wrapper absent")
                        found_widget={cell=cell,table=t,row=row,handler=h.onClick,wrapper=wrapper,
                            id=cell.id,tableid=t.id,index=ri,enabled=state.active}
                    end
                end
            end
        end
        return m,f,model,c,instance,assert(found_widget,"native communication row absent")
    end
    local function fail(why) return {status="REFUSED",reason=tostring(why),action_attempted=false} end
    function api.observe()
        local ok,out=pcall(function()
            local before,age=stamp();local _,f,model,_,_,w=current();local after,ended=stamp()
            assert(same(before,after) and ended>=age,"lifecycle changed during read")
            return {status="OBSERVED",actor_context=model,frame=f.id,widget=w.id,enabled=w.enabled,
                stamp=before,age=age,finished_age=ended,scope="BUILT_MAP_ACTOR_COMM_WIDGET",
                rendered_pixels_verified=false,conversation_actor_verified=false,atomic=false}
        end)
        return ok and out or fail(out)
    end
    function api.prepare(context_token,kind,actor_token)
        local ok,out=pcall(function()
            local s,age=stamp();assert(serial<128,"comm-menu preparation capacity")
            assert(kind=="ENTITY" or kind=="PERSON","typed expected actor required")
            local target=id(d.resolve_issued(context_token,"object"));local expected=id(actor_token)
            local m,f,model,c,instance,w=current()
            assert(model.component==target and model.kind==kind
                and (kind=="PERSON" and model.person or model.entity)==expected,"wrong displayed actor context")
            assert(w.enabled,"communication button disabled")
            local after,ended=stamp();assert(same(s,after) and ended>=age,"lifecycle changed during preparation")
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={stamp=s,age=ended,menu=m,frame=f,frameid=f.id,model=model,context=c,instance=instance,
                widget=w,context_token=context_token,target=target,consumed=false,attempted=false}
            return {status="PREPARED",ticket=ticket,actor_context=copy(model),action_attempted=false}
        end)
        return ok and out or fail(out)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r then return fail("unknown private communication ticket") end
        if r.consumed then return {status=r.status or "UNCERTAIN",action_attempted=r.attempted,reason="ticket consumed"} end
        r.consumed=true
        local ok,why=pcall(function()
            local s,age=stamp();assert(same(s,r.stamp) and age>=r.age and age-r.age<=30,"stale menu lifetime")
            assert(id(d.resolve_issued(r.context_token,"object"))==r.target,"issued context changed")
            local m,f,model,c,instance,w=current()
            assert(m==r.menu and f==r.frame and f.id==r.frameid and c==r.context and instance==r.instance
                and same(model,r.model),"native context rebound")
            local old=r.widget
            assert(w.cell==old.cell and w.table==old.table and w.row==old.row and w.id==old.id
                and w.tableid==old.tableid and w.index==old.index and w.handler==old.handler
                and w.wrapper==old.wrapper and w.enabled,"native communication widget changed")
            local final,ended=stamp();assert(same(s,final) and ended>=age,"lifecycle changed before dispatch")
            r.attempted=true;w.wrapper() -- actual registered native button wrapper, same as a click
            local after,finished=stamp();assert(same(final,after) and finished>=ended,"post-dispatch lifecycle unavailable")
        end)
        r.status=ok and "DISPATCHED_UNVERIFIED" or r.attempted and "UNCERTAIN" or "REFUSED"
        return {status=r.status,action_attempted=r.attempted,reason=ok and "native wrapper returned; opening not yet observed" or tostring(why),
            actor_context=copy(r.model),conversation_actor_verified=false,rendered_dialogue_verified=false}
    end
    function api.dispose() retired=true;return true end
    return api
end

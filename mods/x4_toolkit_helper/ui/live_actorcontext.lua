-- Native MapMenu right-click route: menu_map.lua28477-28643 reads the actual
-- rowDataMap before prepareInfoContext28648-28691 performs native eligibility.
-- Actor rows15300/15373; right info frame19624-19679, registered layer5 at7052.
return function(d)
    for _,k in ipairs({'menus','public_current','clock','resolve_issued','component_id','mouse_position','table_valid'}) do
        assert(type(d[k])=='function','missing actor-context dependency: '..k)
    end
    local api,records,serial,retired={}, {},0,false
    local kinds={info_manager='ENTITY',info_pilot='ENTITY',info_shiptrader='ENTITY',
        info_individualtrainee='ENTITY',info_crewnpc='ENTITY',info_crewperson='PERSON'}
    local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,low,high) return finite(v) and v%1==0 and v>=low and v<=high end
    local function id(v)
        if type(v)=='cdata' then v=tostring(v):match('^(%d+)ULL$') end
        if type(v)=='number' then assert(integer(v,1,9007199254740991),'inexact numeric identity');v=string.format('%.0f',v) end
        assert(type(v)=='string' and v:match('^[1-9]%d*$') and #v<=20
            and (#v<20 or v<='18446744073709551615'),'exact positive identity required');return v
    end
    local function seq(t,cap)
        assert(type(t)=='table' and #t<=cap,'bounded native list required')
        local n=0;for k in pairs(t) do assert(integer(k,1,#t),'sparse native list');n=n+1 end
        assert(n==#t,'sparse native list');return t
    end
    local function copy(t) local out={};for k,v in pairs(t) do out[k]=v end;return out end
    local function same(a,b) for k,v in pairs(a) do if b[k]~=v then return false end end;return true end
    local function stamp()
        assert(not retired,'actor-context retired');local s=d.public_current();local out={}
        assert(type(s)=='table' and s.provider=='x4-live-session-v1','public identity unavailable')
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do
            assert(type(s[k])=='string' and #s[k]>0,'incomplete public stamp');out[k]=s[k]
        end
        local age=d.clock();assert(finite(age) and age>=0,'clock unavailable');return out,age
    end
    local function current(side)
        assert(side=='left' or side=='right','explicit actor pane required')
        local layer=side=='left' and 4 or 5
        local m
        for _,v in ipairs(seq(d.menus(),256)) do
            assert(type(v)=='table','malformed registered menu')
            if v.name=='MapMenu' then assert(not m,'duplicate MapMenu');m=v else assert(not v.shown,'other menu open') end
        end
        assert(m and m.shown==true and m.minimized==false and not m.showMultiverse
            and (not m.panelMode or type(m.panelState)=='table' and m.panelState[side..'menu']==true)
            and m.mode==nil and m.contextMenuMode==nil and m[side=='left' and 'infoTableMode' or 'searchTableMode']=='info'
            and type(m.infoMode)=='table' and m.infoMode[side]=='objectinfo','unsupported native map state')
        assert(not m[side=='left' and 'createInfoFrameRunning' or 'createInfoFrame2Running'],'native info frame rebuilding')
        assert(type(m.onTableRightMouseClick)=='function' and type(m.prepareInfoContext)=='function','native handler unavailable')
        local f=m[side=='left' and 'infoFrame' or 'infoFrame2']
        local tableid=m[side=='left' and 'infoTable' or 'infoTableRight']
        assert(type(f)=='table' and integer(f.id,1,2147483647) and type(f.properties)=='table' and f.properties.layer==layer
            and type(m.frames)=='table' and m.frames[layer]==f.id,'info frame unavailable')
        assert(integer(tableid,1,2147483647) and m.infoTableRight~=m.infoTable,'info table ambiguous')
        local tableobj
        for _,t in ipairs(seq(f.content,64)) do
            if t.type=='table' and t.id==tableid then assert(not tableobj,'duplicate info table');tableobj=t end
        end
        assert(tableobj and d.table_valid(tableobj.id)==true,'native info table invalid')
        local map=type(m.rowDataMap)=='table' and m.rowDataMap[tableobj.id]
        assert(type(map)=='table','native row map unavailable')
        local fixed=m.numFixedRows or 0;assert(integer(fixed,0,512),'invalid fixed row bound')
        local rows={}
        for i,row in ipairs(seq(tableobj.rows,512)) do
            local raw=row.rowdata
            if type(raw)=='table' and kinds[raw[1]] then
                assert(row.index==i and i>fixed and map[i]==raw and #raw==3,'unbound actor row')
                assert(type(raw[3])=='number','native context is not Lua component ID')
                local kind=kinds[raw[1]]
                local model={role=raw[1],kind=kind,context=id(d.component_id(raw[3])),
                    actor=kind=='PERSON' and id(raw[2]) or id(d.component_id(raw[2])),row=i}
                rows[#rows+1]={model=model,row=row,raw=raw}
            end
        end
        return m,f,tableobj,rows
    end
    local function refused(why) return {status='REFUSED',reason=tostring(why),action_attempted=false} end
    function api.observe(side)
        side=side or 'right' -- preserve the historical explicitly right-only caller
        local ok,out=pcall(function()
            local before,age=stamp();local _,f,t,rows=current(side);local result={}
            for _,r in ipairs(rows) do result[#result+1]=copy(r.model) end
            local after,ended=stamp();assert(same(before,after) and ended>=age,'read lifecycle changed')
            return {status='OBSERVED',rows=result,frame=f.id,table=t.id,stamp=before,
                side=side,scope='BUILT_INFO_ACTOR_ROWS',rendered_pixels_verified=false,atomic=false}
        end);return ok and out or refused(out)
    end
    function api.prepare(context_token,role,actor,side)
        side=side or 'right'
        local ok,out=pcall(function()
            local before,age=stamp();assert(serial<128 and kinds[role],'closed actor role/capacity')
            local expected=id(actor);local target=id(d.resolve_issued(context_token,'object'))
            local m,f,t,rows=current(side);local selected
            for _,r in ipairs(rows) do
                if r.model.context==target and r.model.role==role and r.model.actor==expected then
                    assert(not selected,'ambiguous actor row');selected=r
                end
            end
            assert(selected,'expected displayed actor absent')
            local x,y=d.mouse_position();assert(finite(x) and finite(y),'native mouse position unavailable')
            local after,ended=stamp();assert(same(before,after) and ended>=age,'prepare lifecycle changed')
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={stamp=before,age=ended,side=side,menu=m,frame=f,frameid=f.id,table=t,tableid=t.id,selected=selected,
                handler=m.onTableRightMouseClick,prepare=m.prepareInfoContext,token=context_token,target=target,consumed=false,attempted=false}
            return {status='PREPARED',ticket=ticket,actor=copy(selected.model),action_attempted=false}
        end);return ok and out or refused(out)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r then return refused('unknown actor-context ticket') end
        if r.consumed then return {status=r.status or 'UNCERTAIN',action_attempted=r.attempted,reason='ticket consumed'} end
        r.consumed=true
        local ok,why=pcall(function()
            local before,age=stamp();assert(same(before,r.stamp) and age>=r.age and age-r.age<=30,'stale actor-context lifetime')
            assert(id(d.resolve_issued(r.token,'object'))==r.target,'issued context changed')
            local m,f,t,rows=current(r.side);assert(m==r.menu and f==r.frame and f.id==r.frameid and t==r.table and t.id==r.tableid
                and m.onTableRightMouseClick==r.handler and m.prepareInfoContext==r.prepare,'native info model replaced')
            local found
            for _,v in ipairs(rows) do if v.model.row==r.selected.model.row then found=v end end
            assert(found and found.row==r.selected.row and found.raw==r.selected.raw and same(found.model,r.selected.model),'actor row changed')
            local x,y=d.mouse_position();assert(finite(x) and finite(y),'native mouse position unavailable')
            local final,ended=stamp();assert(same(before,final) and ended>=age,'pre-dispatch lifecycle changed')
            r.attempted=true;r.handler(t.id,found.model.row,nil,nil)
            local after,finished=stamp();assert(same(final,after) and finished>=ended,'post-dispatch lifecycle unavailable')
        end)
        r.status=ok and 'DISPATCHED_UNVERIFIED' or r.attempted and 'UNCERTAIN' or 'REFUSED'
        return {status=r.status,action_attempted=r.attempted,reason=ok and 'native right-click handler returned; inspect comm context separately' or tostring(why),
            actor=copy(r.selected.model),context_open_verified=false,conversation_open_verified=false}
    end
    function api.dispose() retired=true;return true end
    return api
end

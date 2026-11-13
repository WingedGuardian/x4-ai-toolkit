-- Native EncyclopediaMenu entry/row routes and MapMenu property descriptors.
-- Helper9782, InteractMenu2390, Encyclopedia1965, Map19853/27064/8987.
-- No constructor native calls; observed row handles are local to one retained window.
return function(deps)
    local d={}
    for _,k in ipairs({'menus','current','now','encode','resolve_issued','component_id','entry_for_component',
        'unlocked','open_menu','convert_parameters','valid_widget','cell_id','widget_state'})do
        assert(type(deps[k])=='function','text navigation dependency: '..k);d[k]=deps[k]
    end
    local cap=128
    local profile=deps.capacity_profile;if profile==nil then profile='interactive' end
    assert(profile=='interactive' or profile=='campaign_v1','unknown capacity profile')
    if deps.capacity_resolver~=nil then
        assert(type(deps.capacity_resolver)=='function','trusted resolver required')
        local c=deps.capacity_resolver(profile)
        assert(type(c)=='table' and c.name==profile
            and type(c.native_history)=='number' and c.native_history%1==0 and c.native_history>=1
            and c.native_history<=512,'trusted native capacity');cap=c.native_history
    else assert(deps.capacity_profile==nil or deps.capacity_profile=='interactive','campaign resolver required')end
    local api,records,count,views,window,retired,bound,last_age={}, {},0,0,nil,false,nil,nil
    local function finite(v)return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function int(v,a,b)return finite(v) and v%1==0 and v>=a and v<=b end
    local function seq(v,n)
        assert(type(v)=='table' and #v<=n,'bounded native sequence');local c=0
        for k in pairs(v)do assert(int(k,1,#v),'sparse native sequence');c=c+1 end
        assert(c==#v,'sparse native sequence');return v
    end
    local function same(a,b)
        if type(a)~=type(b)then return false end;if type(a)~='table'then return a==b end
        for k,v in pairs(a)do if not same(v,b[k])then return false end end
        for k in pairs(b)do if a[k]==nil then return false end end;return true
    end
    local function copy(a)if type(a)~='table'then return a end;local b={};for k,v in pairs(a)do b[k]=copy(v)end;return b end
    local function text(v)return type(v)=='string' and #v>0 and #v<=256 and not v:find('[%c]')end
    local function id(v)
        if type(v)=='cdata'then v=tostring(v):match('^(%d+)ULL$')end
        if type(v)=='number'then assert(int(v,1,9007199254740991),'inexact ID');v=string.format('%.0f',v)end
        assert(type(v)=='string' and v:match('^[1-9]%d*$') and #v<=20 and
            (#v<20 or v<='18446744073709551615'),'exact ID');return v
    end
    local function stamp()
        assert(not retired,'navigation retired');local ok,s=pcall(d.current);local out={}
        if not ok or type(s)~='table' or s.provider~='x4-live-session-v1' then
            if bound then retired=true end;error('public identity unavailable')
        end
        for _,k in ipairs({'provider','revision','session','generation','world_token'})do
            local valid=k=='world_token' and type(s[k])=='string' and #s[k]>0 and #s[k]<=4096 and not s[k]:find('%z')
                or k~='world_token' and text(s[k])
            if not valid or (bound and bound[k]~=s[k])then retired=bound~=nil;error('public identity changed/unavailable')end
            out[k]=s[k]
        end
        local clock_ok,t=pcall(d.now)
        if not clock_ok or not finite(t) or t<0 or last_age and t<last_age then retired=bound~=nil;error('UI clock unavailable/regressed')end
        last_age=t;bound=bound or copy(out);return out,t
    end
    local function finish(s,t)
        local a,b=stamp();assert(same(s,a) and b>=t,'lifecycle changed');return b
    end
    local function menu(name,closed)
        local m
        for _,v in ipairs(seq(d.menus(),256))do
            assert(type(v)=='table','native registry row')
            if v.name==name then assert(not m,'duplicate menu');m=v
            else assert(not v.shown,'another menu shown')end
        end
        assert(m,'menu missing')
        if closed then assert(not m.shown,'menu already shown')
        else
            assert(m.shown==true and m.minimized==false,'menu not shown')
            if name=='MapMenu'then assert(m.mode==nil and m.contextMenuMode==nil and not m.showMultiverse
                and not m.createMainFrameRunning and not m.createInfoFrameRunning,'unsupported map mode/rebuild')
            else assert(m.encyclopediaMode=='encyclopedia' and m.displayRunning==nil and not m.refresh,'encyclopedia rebuilding')end
        end
        return m
    end
    local function table_at(m,domain)
        local f,tid,columns
        if domain=='encyclopedia'then f=m.frame;tid=m.table_index;columns=6
        elseif domain=='property'then
            assert(m.infoTableMode=='propertyowned' and type(m.panelState)=='table'
                and m.panelState.leftmenu==true,'property panel hidden')
            f=m.infoFrame;tid=m.infoTable;columns=10
        else
            f=m.mainFrame;tid=m.sideBar;columns=1
            assert(type(m.panelState)=='table' and m.panelState.leftbar==true,'sidebar hidden')
        end
        assert(type(f)=='table' and int(f.id,1,2147483647) and type(f.properties)=='table'
            and type(m.frames)=='table' and m.frames[f.properties.layer]==f.id and d.valid_widget(f.id)==true,'frame unbound')
        if domain=='property'then assert(f.properties.layer==4,'property layer')end
        if domain=='sidebar'then assert(f.properties.layer==6,'sidebar layer')end
        local t
        for _,v in ipairs(seq(f.content,64))do
            if v.type=='table' and v.id==tid then assert(not t,'duplicate table');t=v end
        end
        assert(t and int(tid,1,2147483647) and t.numcolumns==columns and d.valid_widget(tid)==true,'table unbound')
        seq(t.rows,4096);return f,t
    end
    local function scalar(v)
        if type(v)=='string'then assert(#v<=4096 and not v:find('%z'),'descriptor string bound');return {status='OBSERVED',value=v}end
        return {status='UNKNOWN',lua_type=type(v)}
    end
    local function row_state(m,f,t,ri,domain)
        local r=t.rows[ri];assert(type(r)=='table' and r.index==ri,'row unbound')
        assert(type(m.rowDataMap)=='table' and type(m.rowDataMap[t.id])=='table'
            and m.rowDataMap[t.id][ri]==r.rowdata,'rowdata unbound')
        local out={row=ri,cells={},domain=domain};local refs={m,f,t,r,r.rowdata}
        local rd=r.rowdata
        if domain=='property' and type(rd)=='table' and rd[1]=='property'then
            out.component=id(d.component_id(rd[2]));out.kind='PROPERTY_COMPONENT'
        elseif domain=='encyclopedia' and type(rd)=='table' and type(rd[3])=='table'
            and text(rd[1]) and text(rd[2]) and text(rd[3].id)then
            out.kind='ENTRY';out.mode=rd[1];out.library=rd[2];out.entry=rd[3].id
        else out.kind='CATEGORY_OR_OTHER'end
        for ci=1,t.numcolumns do
            local c=r[ci];assert(type(c)=='table' and type(c.type)=='string' and int(c.colspan,0,t.numcolumns),'native cell')
            refs[#refs+1]=c
            if c.colspan~=0 then
                assert(int(c.id,1,2147483647) and d.valid_widget(c.id)==true
                    and d.cell_id(t.id,ri,ci)==c.id and type(c.properties)=='table','cell unbound')
                refs[#refs+1]=c.properties
                local item={column=ci,type=c.type}
                if c.type=='text'then item.text=scalar(c.properties.text)
                elseif c.type=='icon'then item.icon=scalar(c.properties.icon)end
                out.cells[#out.cells+1]=item
            end
        end
        return out,refs
    end
    local function refs_equal(a,b)if #a~=#b then return false end;for i=1,#a do if a[i]~=b[i]then return false end end;return true end
    local function button(m,f,t,ri)
        local found
        for ci,c in ipairs(t.rows[ri])do
            if c.type=='button' and c.colspan~=0 then
                assert(not found and type(c.handlers)=='table' and type(c.handlers.onClick)=='function','ambiguous button')
                local state=d.widget_state(c.id)
                assert(type(state)=='table' and state.active==true and c.properties.active==true,'button inactive/unknown')
                local wrapper
                for _,w in ipairs(seq(m.buttonScriptMap,4096))do
                    if w.layer==f.properties.layer and w.tableobj==t.id and w.row==ri and w.col==ci and w.type=='onClick'then
                        assert(not wrapper and w.button==c.id and type(w.script)=='function','wrapper mismatch');wrapper=w.script
                    end
                end
                assert(wrapper,'registered button wrapper missing')
                found={cell=c,handler=c.handlers.onClick,wrapper=wrapper,widget=c.id}
            end
        end
        assert(found,'row button unavailable');return found
    end
    local function failure(e)return {status='REFUSED',reason=tostring(e):sub(1,512),action_attempted=false}end
    function api.observe(domain,start)
        local ok,out=pcall(function()
            assert(domain=='encyclopedia' or domain=='property','closed model domain')
            assert(int(start,1,4097) and views<cap,'window/capacity');local s,age=stamp()
            local m=menu(domain=='encyclopedia' and 'EncyclopediaMenu' or 'MapMenu')
            local f,t=table_at(m,domain);assert(start<=#t.rows+1,'offset outside built table')
            local frameid,tableid=f.id,t.id
            local result={status='OBSERVED',domain=domain,total_built_rows=#t.rows,start=start,rows={},
                rendered_pixels_verified=false,full_catalogue=false,atomic=false,dynamic_callbacks_invoked=false}
            if domain=='encyclopedia'then result.context={mode=scalar(m.mode),library=scalar(m.library),id=scalar(m.id)}
            else result.context={property_mode=scalar(m.propertyMode),filter_scope='CURRENT_BUILT_ROWS_ONLY'}end
            local held={}
            for ri=start,math.min(#t.rows,start+3)do
                local a,ar=row_state(m,f,t,ri,domain);local b,br=row_state(m,f,t,ri,domain)
                assert(same(a,b) and refs_equal(ar,br),'row changed during observation')
                local token=tostring(views+1)..':'..tostring(ri);a.token=token
                result.rows[#result.rows+1]=a;held[token]={domain=domain,ri=ri,model=b,refs=br,m=m,f=f,t=t,
                    frameid=f.id,tableid=t.id,stamp=s,age=age}
            end
            result.next_index=start+#result.rows<=#t.rows and start+#result.rows or 0
            assert(#d.encode(result)<=30000,'model page budget');finish(s,age)
            local final_m=menu(domain=='encyclopedia' and 'EncyclopediaMenu' or 'MapMenu')
            local final_f,final_t=table_at(final_m,domain)
            assert(final_m==m and final_f==f and final_t==t and f.id==frameid and t.id==tableid
                and #t.rows==result.total_built_rows,'view changed before publication')
            for _,r in pairs(held)do
                local model,refs=row_state(m,f,t,r.ri,domain)
                assert(same(model,r.model) and refs_equal(refs,r.refs),'row changed before publication')
            end
            finish(s,age)
            views=views+1;window=held;return result
        end);return ok and out or failure(out)
    end
    local function retained(token)
        local r=window and window[token];assert(r,'observed row token absent/superseded')
        local m=menu(r.domain=='encyclopedia' and 'EncyclopediaMenu' or 'MapMenu');local f,t=table_at(m,r.domain)
        assert(m==r.m and f==r.f and t==r.t and f.id==r.frameid and t.id==r.tableid,'view rebuilt')
        local model,refs=row_state(m,f,t,r.ri,r.domain)
        assert(same(model,r.model) and refs_equal(refs,r.refs),'observed row changed')
        return r,m,f,t
    end
    local function opening(action,subject)
        local name=action=='map_open' and 'MapMenu' or 'EncyclopediaMenu'
        local m=menu(name,true);assert(d.unlocked(name)==true,'story feature locked/unknown')
        local params={0,0};local target
        if action=='map_open'then params[3]=true
        elseif action=='component_open'then
            target=id(d.resolve_issued(subject,'object'));local entry=d.entry_for_component(subject)
            assert(type(entry)=='table' and id(entry.component)==target and
                (entry.mode=='Ships' or entry.mode=='Stations') and text(entry.library) and text(entry.id),'component entry unavailable')
            params={0,0,entry.mode,entry.library,entry.id}
        end
        return {menu=m,name=name,params=params,target=target}
    end
    function api.prepare(action,arg)
        local ok,out=pcall(function()
            assert(count<cap,'navigation history capacity');local s,age=stamp();local r={action=action,arg=arg,stamp=s,age=age}
            if action=='root_open' or action=='map_open' or action=='component_open'then
                assert(action=='component_open' and text(arg) or arg==nil,'open arguments');r.open=opening(action,arg)
            elseif action=='close'then
                assert(arg=='encyclopedia' or arg=='property','closed menu domain')
                local m=menu(arg=='encyclopedia' and 'EncyclopediaMenu' or 'MapMenu')
                assert(type(m.onCloseElement)=='function','native close handler unavailable')
                local f=arg=='encyclopedia' and m.frame or m.mainFrame
                assert(type(f)=='table' and int(f.id,1,2147483647) and type(f.properties)=='table'
                    and m.frames[f.properties.layer]==f.id and d.valid_widget(f.id)==true,'close frame unbound')
                r.close={menu=m,handler=m.onCloseElement,name=m.name,frame=f,frameid=f.id,layer=f.properties.layer}
            elseif action=='select' or action=='expand'then
                local v,m,f,t=retained(arg);assert(same(v.stamp,s) and age>=v.age and age-v.age<=15,'stale observed row')
                r.view=v
                if action=='select'then
                    assert(v.domain=='encyclopedia' and v.model.kind=='ENTRY' and type(m.rowChanged)=='function'
                        and type(m.onRowChanged)=='function','select exact encyclopedia entry only')
                    r.wrapper=m.rowChanged;r.handler=m.onRowChanged
                else r.button=button(m,f,t,v.ri)end
            elseif action=='property_open'then
                assert(arg==nil,'property arguments');local m=menu('MapMenu');local f,t=table_at(m,'sidebar');local ri
                for i,row in ipairs(t.rows)do if row.rowdata=='propertyowned'then assert(not ri,'duplicate property row');ri=i end end
                assert(ri,'property sidebar row absent');local model,refs=row_state(m,f,t,ri,'sidebar')
                r.sidebar={m=m,f=f,t=t,ri=ri,model=model,refs=refs,frameid=f.id,tableid=t.id,
                    panel=m.panelMode,mode=m.infoTableMode,open=m.panelState.leftmenu}
                r.eligibility=button(m,f,t,ri)
                if m.panelMode==true and m.infoTableMode~='propertyowned'then
                    assert(type(m.rowChanged)=='function' and type(m.onRowChanged)=='function','native row wrapper');r.wrapper=m.rowChanged;r.handler=m.onRowChanged
                else
                    assert(not (m.infoTableMode=='propertyowned' and m.panelState.leftmenu==true),'property already open')
                    r.button=button(m,f,t,ri)
                end
            else error('unsupported navigation action')end
            finish(s,age);count=count+1;records[tostring(count)]=r
            return {status='PREPARED',ticket=tostring(count),action_attempted=false}
        end);return ok and out or failure(out)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r then return failure('unknown ticket')end
        if r.consumed then return {status=r.status or 'UNCERTAIN',action_attempted=r.attempted==true,reason='ticket consumed'}end
        r.consumed=true
        local ok,err=pcall(function()
            local s,age=stamp();assert(same(s,r.stamp) and age>=r.age and age-r.age<=15,'ticket expired')
            local call
            if r.close then
                local m=menu(r.close.name);assert(m==r.close.menu and m.onCloseElement==r.close.handler,'close menu rebound')
                local f=r.arg=='encyclopedia' and m.frame or m.mainFrame
                assert(f==r.close.frame and f.id==r.close.frameid and f.properties.layer==r.close.layer
                    and m.frames[r.close.layer]==f.id and d.valid_widget(f.id)==true,'close frame rebound')
                call=function()r.close.handler('close')end
            elseif r.open then
                local o=opening(r.action,r.arg);assert(o.menu==r.open.menu and o.target==r.open.target and same(o.params,r.open.params),'entry rebound')
                local p=d.convert_parameters(o.params);assert(type(p)=='table','native conversion unavailable')
                call=function()d.open_menu(o.name,p)end
            else
                local m,f,t,ri
                if r.view then local v;v,m,f,t=retained(r.arg);assert(v==r.view,'token replaced');ri=v.ri
                else
                    local v=r.sidebar;m=menu('MapMenu');f,t=table_at(m,'sidebar');ri=v.ri
                    local model,refs=row_state(m,f,t,ri,'sidebar')
                    assert(m==v.m and f==v.f and t==v.t and f.id==v.frameid and t.id==v.tableid
                        and same(model,v.model) and refs_equal(refs,v.refs) and m.panelMode==v.panel
                        and m.infoTableMode==v.mode and m.panelState.leftmenu==v.open,'property route changed')
                end
                if r.button then
                    local w=button(m,f,t,ri);assert(w.cell==r.button.cell and w.handler==r.button.handler
                        and w.wrapper==r.button.wrapper and w.widget==r.button.widget,'button rebound');call=w.wrapper
                else
                    if r.eligibility then
                        local w=button(m,f,t,ri)
                        assert(w.cell==r.eligibility.cell and w.handler==r.eligibility.handler
                            and w.wrapper==r.eligibility.wrapper and w.widget==r.eligibility.widget,'sidebar eligibility changed')
                    end
                    assert(m.rowChanged==r.wrapper and m.onRowChanged==r.handler,'row handler rebound')
                    call=function()r.wrapper(t.id,ri)end
                end
            end
            local ended=finish(s,age);assert(ended-r.age<=15,'expired before dispatch')
            r.attempted=true;call();finish(s,ended)
        end)
        r.status=ok and 'DISPATCHED_UNVERIFIED' or r.attempted and 'UNCERTAIN' or 'REFUSED'
        return {status=r.status,action_attempted=r.attempted==true,reason=ok and 'native UI route returned; inspect subsequent model' or tostring(err):sub(1,512),
            rendered_pixels_verified=false,selection_verified=false,known_item_read_state_may_change=true}
    end
    function api.capacity()return {history_used=count,history_limit=cap,windows_used=views,windows_limit=cap,retired=retired}end
    function api.dispose()retired=true;window=nil;return true end
    return api
end

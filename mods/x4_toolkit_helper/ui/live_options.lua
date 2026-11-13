-- Built in-world OptionsMenu, never a direct MD setting write.
-- Native gameoptions.lua3615/4713/13493; Helper dropdown wrappers2731-2777.
-- Installed SN API options_menu.lua504-583/632-636 and interface.lua542-571
-- retain authored option.value but replace native option.id with its 1-based index.
return function(deps)
    local d={}
    for _,k in ipairs({'menus','current','now','valid_widget','cell_id','widget_state','set_dropdown_current','open_menu'}) do
        assert(type(deps[k])=='function','missing options dependency: '..k);d[k]=deps[k]
    end
    local api,records,serial,disposed={}, {},0,false
    local MAX_TICKETS=128
    local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,a,b) return finite(v) and v%1==0 and v>=a and v<=b end
    local function text(v,n) assert(type(v)=='string' and #v<=n and not v:find('%z'),'bounded text required');return v end
    local function token(v) text(v,256);assert(v~='' and not v:find('[%c]'),'nonempty token required');return v end
    local function wid(v) return integer(v,1,2147483647) end
    local function seq(t,cap)
        assert(type(t)=='table' and #t<=cap,'bounded native list required')
        local n=0;for k in pairs(t) do assert(integer(k,1,#t),'sparse native list');n=n+1 end
        assert(n==#t,'sparse native list');return t
    end
    local function copy(t) local out={};for k,v in pairs(t) do out[k]=type(v)=='table' and copy(v) or v end;return out end
    local function same(a,b)
        if type(a)~=type(b) then return false end
        if type(a)~='table' then return a==b end
        for k,v in pairs(a) do if not same(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end;return true
    end
    local function stamp()
        assert(not disposed,'options adapter disposed');local s=d.current();local out={}
        assert(type(s)=='table' and s.provider=='x4-live-session-v1','public identity unavailable')
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do out[k]=token(s[k]) end
        local age=d.now();assert(finite(age) and age>=0,'clock unavailable');return out,age
    end
    local function registry(closed)
        local found
        for _,m in ipairs(seq(d.menus(),256)) do
            assert(type(m)=='table','malformed menu registry')
            if m.name=='OptionsMenu' then assert(not found,'duplicate OptionsMenu');found=m
            else assert(not m.shown,'another menu is shown') end
        end
        local m=assert(found,'OptionsMenu missing')
        assert(m.isStartmenu==false,'world OptionsMenu required')
        if closed then assert(not m.shown,'OptionsMenu already open')
        else assert(m.shown==true and m.minimized==false,'OptionsMenu not shown') end
        assert(not m.contextMenuMode and not m.contextFrame and not m.animationDelay and not m.userQuestion,'modal or transition open')
        return m
    end
    local function frame(page)
        token(page);local m=registry(false);assert(m.currentOption==page,'wrong OptionsMenu page')
        local f=m.optionsFrame
        assert(type(f)=='table' and wid(f.id) and type(f.properties)=='table' and f.properties.layer==4
            and type(m.frames)=='table' and m.frames[4]==f.id and d.valid_widget(f.id)==true,'options frame unbound')
        assert(wid(m.optionTable),'option table unknown');local t
        for _,v in ipairs(seq(f.content,64)) do
            if v.type=='table' and v.id==m.optionTable then assert(not t,'duplicate option table');t=v end
        end
        assert(t and d.valid_widget(t.id)==true,'option table not built');seq(t.rows,512)
        assert(type(m.rowDataMap)=='table' and type(m.rowDataMap[t.id])=='table','native row mapping unavailable')
        return m,f,t
    end
    local function native_id(v)
        if type(v)=='number' then assert(integer(v,1,9007199254740991),'inexact option index');v=string.format('%.0f',v) end
        return token(v)
    end
    local function scalar(v)
        if type(v)=='string' then return 'string',text(v,4096) end
        if type(v)=='boolean' then return 'boolean',v and 'true' or 'false' end
        if finite(v) then return 'number',string.format('%.17g',v) end
        return 'UNKNOWN','UNKNOWN'
    end
    local function submenu(m,t,i)
        local row=t.rows[i];assert(row and row.index==i,'row missing')
        local raw=row.rowdata;assert(m.rowDataMap[t.id][i]==raw,'native row map drift')
        assert(type(raw)=='table' and type(row.properties)=='table' and row.properties.interactive==true,'row not selectable')
        assert(raw.callback==nil and raw.value==nil,'not a pure submenu row')
        return {row=i,id=token(raw.id),submenu=token(raw.submenu),text=text(raw.name,4096)},raw,row
    end
    local function dropdown(m,f,t,ri,ci)
        assert(integer(ri,1,512) and integer(ci,1,64),'bounded cell coordinates required')
        local row=t.rows[ri];assert(row and row.index==ri,'dropdown row missing')
        assert(m.rowDataMap[t.id][ri]==row.rowdata,'dropdown row map drift')
        local c=row[ci]
        assert(type(c)=='table' and c.type=='dropdown' and c.colspan~=0 and wid(c.id)
            and d.valid_widget(c.id)==true and d.cell_id(t.id,ri,ci)==c.id,'dropdown not built')
        local p,h=c.properties,c.handlers
        assert(type(p)=='table' and type(p.active)=='boolean' and type(h)=='table'
            and type(h.onDropDownConfirmed)=='function','dropdown descriptor unavailable')
        local n=d.widget_state(c.id,'dropdown')
        assert(type(n)=='table' and type(n.active)=='boolean' and n.active==p.active,'native dropdown enabled-state drift')
        seq(p.options,128);seq(n.options,128);assert(#p.options==#n.options,'native option count mismatch')
        local options,seen={},{}
        for i,v in ipairs(p.options) do
            local actual=n.options[i];assert(type(v)=='table' and type(actual)=='table','option missing')
            local id=native_id(v.id);assert(actual.id==id and not seen[id] and type(actual.active)=='boolean','native option identity mismatch')
            seen[id]=true;assert(v.active==nil or v.active==actual.active,'native option availability mismatch')
            assert(text(v.text,4096)==text(actual.text,4096),'displayed option text mismatch')
            local kind,value=scalar(v.value)
            options[i]={index=i,id=id,text=actual.text,active=actual.active,authored_value_type=kind,authored_value=value}
        end
        local wrapper
        for _,w in ipairs(seq(m.dropdownScriptMap,4096)) do
            if w.layer==4 and w.tableobj==t.id and w.row==ri and w.col==ci and w.type=='onDropDownConfirmed' then
                assert(not wrapper and type(w.script)=='function','ambiguous dropdown wrapper');wrapper=w.script
            end
        end
        assert(wrapper,'registered dropdown wrapper missing')
        return {row=ri,col=ci,widget=c.id,enabled=n.active,options=options},c,row,h.onDropDownConfirmed,wrapper
    end
    -- Native gameoptions.lua4750 assigns option.callback to the built button;
    -- Helper.setButtonScript2662-2706 records the actual onClick wrapper.
    -- This is an observed UI callback, not an arbitrary named function or MD cue.
    local function button(m,f,t,ri,ci)
        assert(integer(ri,1,512) and integer(ci,1,64),'bounded button coordinates')
        local row=t.rows[ri];assert(row and row.index==ri,'button row missing')
        local raw=row.rowdata
        assert(m.rowDataMap[t.id][ri]==raw,'button row map drift')
        local msa=type(raw)=='boolean'
        assert(msa or type(raw)=='table' and raw.valuetype=='button' and raw.submenu==nil
            and type(raw.callback)=='function','plain built callback button required')
        assert(type(row.properties)=='table' and type(row.properties.interactive)=='boolean','button row state unknown')
        local c=row[ci]
        assert(type(c)=='table' and c.type=='button' and c.colspan~=0 and wid(c.id)
            and d.valid_widget(c.id)==true and d.cell_id(t.id,ri,ci)==c.id,'button not built')
        local p,h=c.properties,c.handlers
        assert(type(p)=='table' and type(p.active)=='boolean' and type(h)=='table'
            and type(h.onClick)=='function' and (msa or h.onClick==raw.callback),'button callback unavailable')
        -- SN API interface.lua259-278 uses selectable boolean rowdata; its
        -- Make_Button459-466 registers a callback, with no authored widget ID.
        -- Helper.button:setText5753 retains the static text descriptor.
        local button_id,name,label,kind
        if msa then
            assert(type(p.text)=='table','static MSA button text missing')
            button_id='widget:'..tostring(c.id);name='';label=text(p.text.text,4096)
            kind='BUILT_WIDGET_ONLY'
        else
            button_id=native_id(raw.id);name=text(raw.name,4096);label=text(raw.value,4096)
            kind='NATIVE_OPTION_ROW'
        end
        local native=d.widget_state(c.id,'button')
        assert(type(native)=='table' and type(native.active)=='boolean'
            and native.active==p.active,'native button state drift')
        local wrapper
        for _,w in ipairs(seq(m.buttonScriptMap,4096))do
            if w.layer==4 and w.tableobj==t.id and w.row==ri and w.col==ci and w.type=='onClick'then
                assert(not wrapper and w.button==c.id and type(w.script)=='function','button wrapper ambiguous')
                wrapper=w.script
            end
        end
        assert(wrapper,'registered button wrapper missing')
        return {row=ri,col=ci,widget=c.id,id=button_id,name=name,identity_kind=kind,
            text=label,enabled=native.active and row.properties.interactive and (not msa or raw),
            native_enabled=native.active,row_interactive=row.properties.interactive},c,row,raw,h.onClick,wrapper
    end
    local function fail(why) return {status='REFUSED',reason=tostring(why),action_attempted=false} end
    function api.observe(page)
        local ok,out=pcall(function()
            local before,age=stamp();local m,f,t=frame(page);local links,widgets,buttons={},{},{}
            local button_cells,button_unknown=0,0
            for i,row in ipairs(t.rows) do
                if type(row.rowdata)=='table' and type(row.rowdata.submenu)=='string' and row.rowdata.callback==nil and row.rowdata.value==nil then
                    links[#links+1]=submenu(m,t,i)
                end
                assert(#row<=64,'cell count overflow')
                for ci,c in ipairs(row) do
                    if type(c)=='table' and c.type=='dropdown' then widgets[#widgets+1]=dropdown(m,f,t,i,ci) end
                    if type(c)=='table' and c.type=='button' then
                        button_cells=button_cells+1
                        if type(row.rowdata)=='boolean' or type(row.rowdata)=='table' and row.rowdata.valuetype=='button' then
                            buttons[#buttons+1]=button(m,f,t,i,ci)
                        else button_unknown=button_unknown+1 end
                    end
                end
            end
            assert(#links<=128 and #widgets<=32 and #buttons<=32,'options observation capacity')
            local after,ended=stamp();assert(same(before,after) and ended>=age,'read lifecycle changed')
            return {status='OBSERVED',page=page,frame=f.id,table=t.id,submenus=links,dropdowns=widgets,buttons=buttons,
                button_cells=button_cells,button_cells_unqualified=button_unknown,
                callback_button_scope_complete=button_unknown==0,
                stamp=before,scope='BUILT_OPTIONS_SUBMENUS_DROPDOWNS_AND_CALLBACK_BUTTONS',rendered_pixels_verified=false,
                authored_widget_id='NOT_EXPOSED',setting_effect_verified=false,atomic=false}
        end);return ok and out or fail(out)
    end
    local function store(r)
        assert(serial<MAX_TICKETS,'options request capacity');serial=serial+1;r.ticket=tostring(serial);records[r.ticket]=r
        return {status='PREPARED',ticket=r.ticket,action=r.action,model=r.model and copy(r.model),action_attempted=false}
    end
    function api.prepare_open()
        local ok,out=pcall(function()
            local s,age=stamp();local m=registry(true);local after,ended=stamp()
            assert(same(s,after) and ended>=age,'open preparation lifecycle changed')
            return store{action='open',stamp=s,age=ended,menu=m}
        end);return ok and out or fail(out)
    end
    function api.prepare_submenu(page,id,target)
        local ok,out=pcall(function()
            -- These native branches dispatch submenu rows, not load/save/extension toggles.
            assert(page=='main' or page=='simple_menu_extension_options','unsupported submenu entry page')
            token(id);token(target);local s,age=stamp();local m,f,t=frame(page);local chosen,raw,row
            for i,r in ipairs(t.rows) do
                if type(r.rowdata)=='table' and r.rowdata.id==id then
                    assert(not chosen,'ambiguous submenu id');chosen,raw,row=submenu(m,t,i)
                end
            end
            assert(chosen and chosen.submenu==target,'expected submenu absent')
            assert(type(m.onSelectElement)=='function' and type(m.openSubmenu)=='function' and type(m.submenuHandler)=='function','native submenu handlers missing')
            local after,ended=stamp();assert(same(s,after) and ended>=age,'submenu preparation lifecycle changed')
            return store{action='submenu',stamp=s,age=ended,page=page,menu=m,frame=f,frameid=f.id,table=t,tableid=t.id,
                model=chosen,raw=raw,row=row,select=m.onSelectElement,open=m.openSubmenu,submenu=m.submenuHandler}
        end);return ok and out or fail(out)
    end
    function api.prepare_select(page,ri,ci,widget,option_id,label,value_type,value)
        local ok,out=pcall(function()
            token(option_id);text(label,4096);text(value,4096)
            assert(value_type=='number' or value_type=='string' or value_type=='boolean','authored value mapping required')
            local s,age=stamp();local m,f,t=frame(page);local model,c,row,handler,wrapper=dropdown(m,f,t,ri,ci)
            assert(model.widget==widget and model.enabled,'wrong/disabled dropdown');local selected
            for _,v in ipairs(model.options) do if v.id==option_id then selected=v end end
            assert(selected and selected.active and selected.text==label and selected.authored_value_type==value_type
                and selected.authored_value==value,'displayed index to authored value mismatch')
            local after,ended=stamp();assert(same(s,after) and ended>=age,'select preparation lifecycle changed')
            return store{action='select',stamp=s,age=ended,page=page,menu=m,frame=f,frameid=f.id,table=t,tableid=t.id,
                model=model,cell=c,row=row,handler=handler,wrapper=wrapper,option=option_id,selected=copy(selected)}
        end);return ok and out or fail(out)
    end
    function api.prepare_button(page,ri,ci,widget,id,label)
        local ok,out=pcall(function()
            -- Load/save/new/profile and top-level navigation stay outside this
            -- callback route. This is not an author or side-effect sandbox.
            local reserved={main=true,save=true,load=true,extensions=true,bonus=true,new=true,
                profile_load=true,simple_menu_extension_options=true}
            token(page);token(id);text(label,4096);assert(not reserved[page],'unsupported button page')
            local s,age=stamp();local m,f,t=frame(page)
            local model,c,row,raw,handler,wrapper=button(m,f,t,ri,ci)
            assert(model.widget==widget and model.id==id and model.text==label and model.enabled,
                'expected enabled button absent')
            local after,ended=stamp();assert(same(s,after) and ended>=age,'button lifecycle changed')
            return store{action='button',stamp=s,age=ended,page=page,menu=m,frame=f,frameid=f.id,
                table=t,tableid=t.id,model=model,cell=c,row=row,raw=raw,handler=handler,wrapper=wrapper}
        end);return ok and out or fail(out)
    end
    local function close_handlers(m)
        assert(not m.remapControl and not m.directInputActive and not m.cutsceneid
            and not m.playNewGameCutscene,'special OptionsMenu activity')
        assert(type(m.onCloseElement)=='function' and type(m.closeMenu)=='function'
            and type(m.cleanup)=='function','native close handlers unavailable')
        return m.onCloseElement,m.closeMenu,m.cleanup
    end
    function api.prepare_close(page)
        local ok,out=pcall(function()
            local s,age=stamp();local m,f,t=frame(page)
            local handler,close,cleanup=close_handlers(m)
            local after,ended=stamp();assert(same(s,after) and ended>=age,'close lifecycle changed')
            return store{action='close',stamp=s,age=ended,page=page,menu=m,frame=f,frameid=f.id,
                table=t,tableid=t.id,handler=handler,close=close,cleanup=cleanup}
        end);return ok and out or fail(out)
    end
    function api.observe_closed(ticket)
        local ok,out=pcall(function()
            local r=records[ticket]
            assert(r and r.action=='close' and r.consumed and r.attempted
                and r.status=='DISPATCHED_UNVERIFIED','qualified owned close dispatch required')
            local s,age=stamp();assert(same(s,r.stamp) and age>=r.age,'close identity changed')
            local m=registry(true);assert(m==r.menu,'OptionsMenu registration replaced')
            local after,ended=stamp();assert(same(s,after) and ended>=age,'close read lifecycle changed')
            return {status='MENU_CLOSED_OBSERVED',shown=false,stamp=s,
                shown_normalized=true,native_shown_state=m.shown==nil and 'UNSET' or 'FALSE',
                rendered_pixels_verified=false,simulation_unpaused_verified=false}
        end);return ok and out or fail(out)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r then return fail('unknown options ticket') end
        if r.consumed then return {status=r.status or 'UNCERTAIN',action_attempted=r.attempted==true,reason='ticket consumed'} end
        r.consumed=true
        local ok,why=pcall(function()
            local s,age=stamp();assert(same(s,r.stamp) and age>=r.age and age-r.age<=30,'stale options lifetime')
            local invoke
            if r.action=='open' then
                assert(registry(true)==r.menu,'OptionsMenu registration changed')
                invoke=function() d.open_menu('OptionsMenu','main') end
            else
                local m,f,t=frame(r.page)
                assert(m==r.menu and f==r.frame and f.id==r.frameid and t==r.table and t.id==r.tableid,'options view replaced')
                if r.action=='close' then
                    local handler,close,cleanup=close_handlers(m)
                    assert(handler==r.handler and close==r.close and cleanup==r.cleanup,'close handlers changed')
                    -- gameoptions.lua13671-13736: in-world 'close' calls
                    -- closeMenu/cleanup, without history navigation or quit.
                    invoke=function() handler('close') end
                elseif r.action=='submenu' then
                    local model,raw,row=submenu(m,t,r.model.row)
                    assert(same(model,r.model) and raw==r.raw and row==r.row and m.onSelectElement==r.select
                        and m.openSubmenu==r.open and m.submenuHandler==r.submenu,'submenu row or handler changed')
                    invoke=function() r.select(t.id,false,model.row,false,'mouse') end
                elseif r.action=='button' then
                    local model,c,row,raw,handler,wrapper=button(m,f,t,r.model.row,r.model.col)
                    assert(same(model,r.model) and c==r.cell and row==r.row and raw==r.raw
                        and handler==r.handler and wrapper==r.wrapper and model.enabled,'button model changed')
                    invoke=function() wrapper() end
                else
                    local model,c,row,handler,wrapper=dropdown(m,f,t,r.model.row,r.model.col)
                    assert(same(model,r.model) and c==r.cell and row==r.row and handler==r.handler and wrapper==r.wrapper,'dropdown model changed')
                    invoke=function() d.set_dropdown_current(model.widget,r.option);wrapper(model.widget,r.option) end
                end
            end
            local final,ended=stamp();assert(same(s,final) and ended>=age and ended-r.age<=30,'pre-write lifecycle changed')
            r.attempted=true;invoke()
            local after,finished=stamp();assert(same(final,after) and finished>=ended,'post-write lifecycle unavailable')
        end)
        r.status=ok and 'DISPATCHED_UNVERIFIED' or r.attempted and 'UNCERTAIN' or 'REFUSED'
        return {status=r.status,action=r.action,action_attempted=r.attempted==true,reason=ok and 'native UI handler returned; observe effects separately' or tostring(why),
            selected=r.selected and copy(r.selected),native_pause_possible=r.action=='open' or r.action=='close',setting_effect_verified=false,
            rendered_pixels_verified=false,callback_effects_verified=false,automatic_retry=false}
    end
    function api.dispose() disposed=true;return true end
    function api.capacity()
        return {available=not disposed,budgets={tickets={used=serial,limit=MAX_TICKETS}},
            cached_context='PER_REQUEST_NOT_EXPOSED'}
    end
    return api
end

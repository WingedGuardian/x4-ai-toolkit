-- Native OptionsMenu only. No world-session substitute or automatic NewGame call.
-- Provenance and unimplemented host authorization: docs/live-startmenu-qualification.md.
local function new(d)
    for _,k in ipairs({"menus","now","ui_binding","is_startmenu","cell_id","button_active",
        "valid_widget","current_row","select_row","open_menu","read_text","authorize_start"}) do
        assert(type(d[k])=="function","missing start-menu dependency: "..k)
    end
    local api, records, count, last, disposed = {}, {}, 0, nil, false
    local function finite(n) return type(n)=="number" and n==n and math.abs(n)<=9007199254740991 end
    local function wid(n) return finite(n) and n>0 and n%1==0 and n<=2147483647 end
    local function text(s) return type(s)=="string" and #s<=4096 and not s:find("[%z\1-\8\11\12\14-\26\28-\31]") end
    local function seq(t,cap)
        assert(type(t)=="table" and #t<=cap,"missing/overflow native sequence")
        for k in pairs(t) do assert(type(k)=="number" and k%1==0 and k>=1 and k<=#t,"sparse sequence") end
        return #t
    end
    local function snapshot(value,budget,seen)
        budget=budget or {n=0,bytes=0};seen=seen or {}
        budget.n=budget.n+1;assert(budget.n<=4096,"model node limit")
        local k=type(value)
        if k=="nil" or k=="boolean" then return value,k..":"..tostring(value) end
        if k=="number" then assert(finite(value),"invalid model number");return value,"n:"..string.format("%.17g",value) end
        if k=="string" then
            assert(text(value),"invalid model string");budget.bytes=budget.bytes+#value
            assert(budget.bytes<=65536,"model byte limit");return value,"s:"..#value..":"..value
        end
        assert(k=="table" and not seen[value],"unsupported model value")
        seen[value]=true;local out,parts={},{}
        for key,v in pairs(value) do
            assert(type(key)=="string" or (finite(key) and key%1==0),"invalid model key")
            local ck,fk=snapshot(key,budget,seen);local cv,fv=snapshot(v,budget,seen)
            out[ck]=cv;parts[#parts+1]=fk.."="..#fv..":"..fv
        end
        table.sort(parts);seen[value]=nil;return out,"t{"..table.concat(parts,";").."}"
    end
    local function binding()
        assert(not disposed,"adapter disposed")
        local now=d.now();assert(finite(now) and now>=0 and (not last or now>=last),"invalid/backward clock")
        last=now
        local b=d.ui_binding()
        assert(type(b)=="string" and #b>=1 and #b<=256 and text(b),"UI binding unavailable")
        assert(d.is_startmenu()==true,"not native start menu")
        return b,now
    end
    local function menu()
        local found;local menus=d.menus();seq(menus,256)
        for _,m in ipairs(menus) do
            assert(type(m)=="table","invalid menu registry")
            assert(m.name=="OptionsMenu" or not m.shown,"another menu is shown")
            if m.name=="OptionsMenu" then assert(not found,"ambiguous OptionsMenu");found=m end
        end
        local m=assert(found,"OptionsMenu not registered")
        assert(m.isStartmenu==true and m.shown==true and m.minimized==false,"OptionsMenu not visibly ready")
        assert(not m.contextMenuMode and not m.contextFrame and not m.animationDelay,"modal/transition open")
        return m
    end
    local function built(m)
        assert(m.currentOption=="new","ordinary new-game page required")
        local f=m.optionsFrame
        assert(type(f)=="table" and wid(f.id) and type(f.properties)=="table" and
            type(m.frames)=="table" and m.frames[f.properties.layer]==f.id and
            d.valid_widget(f.id)==true,"current options frame unavailable")
        seq(f.content,64);return f
    end
    local function options(m,f)
        assert(wid(m.optionTable),"option table unavailable")
        local tbl
        for _,t in ipairs(f.content) do
            if t.type=="table" and t.id==m.optionTable then assert(not tbl,"duplicate option table");tbl=t end
        end
        assert(tbl and d.valid_widget(tbl.id)==true,"option table not built")
        seq(tbl.rows,512)
        local mapping=assert(m.rowDataMap and m.rowDataMap[tbl.id],"native row mapping unavailable")
        local out,ids={},{}
        for i,row in ipairs(tbl.rows) do
            local v=mapping[i]
            assert(row.rowdata==v,"row mapping drift")
            if type(v)=="table" then
                assert(text(v.id) and v.id~="" and not ids[v.id],"invalid/duplicate start ID")
                ids[v.id]=true
                local data,fp=snapshot(v)
                out[#out+1]={row=i,data=data,fp=fp,source=v}
            else assert(v==nil or v==false,"unknown option row") end
        end
        return tbl,out
    end
    local function selected(m,f)
        local tbl,rows=options(m,f);local found
        for _,r in ipairs(rows) do if r.source==m.selectedOption then assert(not found,"duplicate selection");found=r end end
        assert(found and d.current_row(tbl.id)==found.row,"native selection readback mismatch")
        return tbl,found
    end
    local function button(m,f)
        local label=d.read_text(1001,9902);assert(text(label) and label~="","Start label unknown")
        local found
        for _,t in ipairs(f.content) do
            if t.type=="table" and type(t.properties)=="table" and t.properties.tabOrder==4 then
                assert(wid(t.id),"start table not built");seq(t.rows,512)
                for ri,row in ipairs(t.rows) do
                    local c=row[1]
                    if c and c.type=="button" and c.colspan==3 and type(c.properties)=="table" and
                        type(c.properties.text)=="table" and c.properties.text.text==label then
                        assert(not found,"ambiguous Start button")
                        assert(wid(c.id) and d.valid_widget(c.id)==true and d.cell_id(t.id,ri,1)==c.id,"stale Start widget")
                        local handler=c.handlers and c.handlers.onClick
                        assert(type(handler)=="function","Start handler unavailable")
                        local active=d.button_active(c.id)
                        assert(type(active)=="boolean" and type(c.properties.active)=="boolean" and
                            active==c.properties.active,"native enabled state unknown/drifted")
                        assert(type(m.buttonStartGameActive)=="function" and
                            m.buttonStartGameActive()==active,"native eligibility changed")
                        local wrapper;seq(m.buttonScriptMap,4096)
                        for _,w in ipairs(m.buttonScriptMap) do
                            if w.layer==f.properties.layer and w.tableobj==t.id and w.row==ri and w.col==1 and w.type=="onClick" then
                                assert(not wrapper and w.button==c.id and type(w.script)=="function","invalid Start wrapper")
                                wrapper=w.script
                            end
                        end
                        assert(wrapper,"Start wrapper absent")
                        found={cell=c,table=t,row=ri,wrapper=wrapper,handler=handler,active=active,id=c.id}
                    end
                end
            end
        end
        return assert(found,"built Start button absent")
    end
    local function ordinary(v)
        assert(v.available==true and v.unlocked==true and v.requirement=="" and
            type(v.extensionid)=="string" and not v.customeditor and not v.mapeditor and
            not v.tutorial and not v.timelinesscenario and v.id~="x4ep1_gamestart_hub",
            "unsupported or unavailable ordinary start")
    end
    local function refuse(err) return {status="REFUSED",reason=tostring(err),action_attempted=false,scope="UI_ONLY_NO_WORLD"} end
    function api.observe()
        local ok,out=pcall(function()
            local b,t=binding();local m=menu();local out={status="OBSERVED",scope="UI_ONLY_NO_WORLD",
                binding=b,age=t,page=m.currentOption or "",world_session=false,atomic=false,entry_route_verified=false}
            if m.currentOption=="new" then
                local f=built(m);local tbl,rows=options(m,f);out.frame=f.id;out.table=tbl.id;out.options={}
                for _,r in ipairs(rows) do out.options[#out.options+1]={row=r.row,option=r.data} end
                out.count=#rows
                local yes,w=pcall(button,m,f);out.start=yes and {status="OBSERVED",enabled=w.active,widget=w.id} or {status="UNKNOWN",reason=tostring(w)}
                out.selected=type(m.selectedOption)=="table" and m.selectedOption.id or ""
                out.current_row=d.current_row(tbl.id)
            end
            assert(binding()==b,"UI binding changed during read");return out
        end)
        return ok and out or refuse(out)
    end
    function api.prepare(id,action,option)
        local ok,out=pcall(function()
            assert(type(id)=="string" and #id==32 and id:match("^[0-9a-f]+$") and not records[id],"fresh request ID required")
            assert(count<128,"start-menu request limit")
            assert(action=="open_new" or action=="select" or action=="start","unknown action")
            local b,t=binding();local m=menu()
            local r={id=id,action=action,option=option,binding=b,age=t,menu=m,page=m.currentOption,
                state="PREPARED",attempted=false}
            if action=="open_new" then
                assert(option==nil and m.currentOption~="new","already open/extra argument")
                assert(type(m.openSubmenu)=="function","native openSubmenu unavailable")
            else
                assert(text(option) and option~="","start ID required")
                local f=built(m);r.frame=f;r.frameid=f.id
                local tbl,rows=options(m,f);r.tbl=tbl
                for _,v in ipairs(rows) do if v.data.id==option then r.selected=v end end
                assert(r.selected,"start is not offered")
                ordinary(r.selected.data)
                if action=="start" then
                    local _,v=selected(m,f);assert(v.source==r.selected.source,"requested start not selected")
                    r.widget=button(m,f);assert(r.widget.active,"native Start disabled")
                end
            end
            assert(binding()==b,"UI binding changed during preparation")
            records[id]=r;count=count+1
            return {status="PREPARED",request_id=id,action=action,option=option,
                binding=b,age=t,scope="UI_ONLY_NO_WORLD",action_attempted=false,world_started=false}
        end)
        return ok and out or refuse(out)
    end
    local function recheck(r)
        local b,t=binding();assert(b==r.binding and t-r.age<=30,"stale preparation")
        local m=menu();assert(m==r.menu and m.currentOption==r.page,"menu changed")
        if r.action=="open_new" then return end
        local f=built(m);assert(f==r.frame and f.id==r.frameid,"frame rebuilt")
        local tbl,rows=options(m,f);assert(tbl==r.tbl,"table rebuilt")
        local selectedrow
        for _,v in ipairs(rows) do if v.data.id==r.option then selectedrow=v end end
        assert(selectedrow and selectedrow.source==r.selected.source and selectedrow.row==r.selected.row and
            selectedrow.fp==r.selected.fp,"start option changed")
        ordinary(selectedrow.data)
        if r.action=="start" then
            local _,v=selected(m,f);assert(v.source==r.selected.source,"selection changed")
            local w=button(m,f)
            assert(w.active and w.cell==r.widget.cell and w.id==r.widget.id and w.table==r.widget.table and
                w.handler==r.widget.handler and w.wrapper==r.widget.wrapper,"Start callback changed/disabled")
        end
    end
    local function result(r)
        return {status=r.state,request_id=r.id,action=r.action,option=r.option,reason=r.reason or "",
            action_attempted=r.attempted,scope="UI_ONLY_NO_WORLD",world_started=false,
            automatic_retry=false,historical=true}
    end
    function api.execute(id)
        local r=records[id]
        if not r or r.state~="PREPARED" then return refuse("unknown or consumed preparation") end
        r.state="CONSUMED"
        local ok,err=pcall(function()
            recheck(r)
            if r.action=="start" then
                r.state="AUTHORIZATION_PENDING"
                local called=false
                -- This callback must be supplied by a trusted, freshly approving host.
                -- A caller JSON flag or constant-true implementation is NOT qualified.
                d.authorize_start({scope="UI_ONLY_NO_WORLD",request_id=r.id,binding=r.binding,
                    gamestart=r.option,prepared_age=r.age},function(approved,reason)
                    if called or r.state~="AUTHORIZATION_PENDING" then return end
                    called=true
                    local good,why=pcall(function()
                        assert(approved==true,"fresh host authorization refused: "..tostring(reason))
                        recheck(r);r.attempted=true;r.state="DISPATCHING"
                        r.widget.wrapper()
                    end)
                    r.state=good and "DISPATCHED_UNVERIFIED" or (r.attempted and "UNKNOWN" or "REFUSED")
                    r.reason=good and "native Start callback returned; new world unproved" or tostring(why)
                end)
            else
                r.attempted=true
                if r.action=="open_new" then
                    -- Vanilla New Game click: menu.openSubmenu(option.submenu, option.id) on the SHOWN options
                    -- menu (ego_gameoptions/gameoptions.lua:5200, called at :13579; option id and submenu "new" at
                    -- :1293-1296). OpenMenu("OptionsMenu",...) on the already-shown menu left the page on main in
                    -- qualification runs 02-04 (INFERRED cause; the first launch with this build qualifies it).
                    r.menu.openSubmenu("new","new")
                else
                    d.select_row(r.tbl.id,r.selected.row)
                    -- SelectRow's public wrapper normally returns nil. Read back instead.
                    local m=menu();local f=built(m);local _,v=selected(m,f)
                    assert(m==r.menu and v.data.id==r.option,"selection not observed")
                end
                r.state=r.action=="select" and "SELECTION_OBSERVED" or "DISPATCHED_UNVERIFIED"
                r.reason="native UI route only; no new-world completion"
            end
        end)
        if not ok then r.state=r.attempted and "UNKNOWN" or "REFUSED";r.reason=tostring(err) end
        return result(r)
    end
    function api.status(id) return records[id] and result(records[id]) or refuse("unknown request") end
    function api.dispose()
        disposed=true
        for _,r in pairs(records) do
            if r.state=="PREPARED" or r.state=="AUTHORIZATION_PENDING" then r.state="REFUSED";r.reason="adapter disposed" end
        end
        return true
    end
    return api
end
local function native(g,C,Helper,ui_binding,authorize_start)
    for _,k in ipairs({"GetCellContent","IsButtonActive","IsValidWidgetElement","SelectRow",
        "OpenMenu","ReadText","getElapsedTime"}) do assert(type(g[k])=="function","native global unavailable: "..k) end
    assert(type(Helper)=="table" and type(Helper.currentTableRow)=="table","native Helper unavailable")
    return new({menus=function() return g.Menus end,now=g.getElapsedTime,ui_binding=ui_binding,
        is_startmenu=function() return C.IsStartmenu() end,cell_id=g.GetCellContent,
        button_active=g.IsButtonActive,valid_widget=g.IsValidWidgetElement,
        current_row=function(id) return Helper.currentTableRow[id] end,
        select_row=g.SelectRow,open_menu=g.OpenMenu,read_text=g.ReadText,authorize_start=authorize_start})
end
return {new=new,native=native}

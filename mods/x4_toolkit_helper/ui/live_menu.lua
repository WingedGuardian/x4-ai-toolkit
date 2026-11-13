-- Actual registered ShipConfigurationMenu adapter; no registration or implicit actions.
-- Native provenance and qualification limits: its live-menu-qualification development record (in the toolkit's git repository, not in release downloads).
local function new(d)
    for _, k in ipairs({"menus", "cell_id", "epoch", "now", "resolve_issued",
        "open_menu", "convert_params", "convert_id", "widget_state", "set_dropdown_current"}) do
        assert(type(d[k]) == "function", "missing menu dependency: " .. k)
    end
    local api, records, serial = {}, {}, 0
    local MAX_TICKETS=128
    local specs = {
        class = {kind="dropdown", id="shipconfig_classoptions", handler="dropdownShipClass"},
        ship = {kind="dropdown", id="shipconfig_shipoptions", handler="dropdownShip"},
        loadout = {kind="dropdown", handler="dropdownLoadout"},
        add = {kind="button", id="shipconfig_addtoshoppinglist"},
        confirm = {kind="button", id="shipconfig_confirm", handler="buttonConfirm"},
        repair_tab = {kind="button", id="shipconfig_leftbar_repair", help=true},
        repair_select = {kind="button", id="shipconfig_repair"},
    }
    local function finite(x)
        return type(x) == "number" and x == x and math.abs(x) < math.huge
    end
    local function widget_id(x)
        return finite(x) and x>0 and x<=2147483647 and x%1==0
    end
    local function text(x)
        return type(x) == "string" and #x <= 4096 and not x:find("[%z\1-\8\11\12\14-\26\28-\31]")
    end
    local function id(x)
        local s = type(x) == "cdata" and tostring(x):match("^(%d+)ULL$") or
            (type(x) == "string" and x)
        assert(s and s:match("^%d+$") and (#s == 1 or s:sub(1,1) ~= "0") and
            #s <= 20 and (#s < 20 or s <= "18446744073709551615"), "unknown component identity")
        return s
    end
    local function sequence(t, limit)
        assert(type(t) == "table", "missing native list")
        local n = #t
        assert(n <= limit, "native list overflow")
        for k in pairs(t) do
            assert(type(k) == "number" and k >= 1 and k <= n and k % 1 == 0,
                "sparse or foreign list key")
        end
        for i=1,n do assert(t[i] ~= nil, "sparse native list") end
        return n
    end
    local function stamp()
        local e, t = d.epoch(), d.now()
        assert(text(e) and e ~= "" and finite(t) and t >= 0, "unknown lifecycle")
        return e, t
    end
    -- Immutable bounded copy for model evidence; fingerprints also bind opaque
    -- function identities. Functions are NEVER invoked to manufacture displayed text.
    local function copy(value, budget, depth, seen)
        budget.n = budget.n + 1
        assert(budget.n <= 12000 and depth <= 16, "native model overflow")
        local kind = type(value)
        if kind == "nil" or kind == "boolean" then return value, kind .. ":" .. tostring(value) end
        if kind == "number" then
            assert(finite(value) and math.abs(value) <= 9007199254740991, "inexact native number")
            return value, "n:" .. string.format("%.17g",value)
        end
        if kind == "string" then
            assert(text(value), "unsupported native string")
            budget.bytes = budget.bytes + #value
            assert(budget.bytes <= 131072, "native model byte overflow")
            return value, "s:" .. #value .. ":" .. value
        end
        if kind == "cdata" then
            local s = tostring(value)
            assert(s:match("^%d+ULL$") or s:match("^%-?%d+LL$"), "unsupported native cdata")
            return {kind="exact_integer", value=s}, "c:" .. s
        end
        if kind == "function" then
            return {kind="unresolved_native_function"}, "f:" .. tostring(value)
        end
        assert(kind == "table" and not seen[value], "unsupported or cyclic native model")
        seen[value] = true
        local result, pieces = {}, {}
        for k,v in pairs(value) do
            assert(type(k) == "string" or (finite(k) and k%1==0), "unsupported native key")
            local ck, fk = copy(k,budget,depth+1,seen)
            local cv, fv = copy(v,budget,depth+1,seen)
            result[ck] = cv
            pieces[#pieces+1] = fk .. "=" .. #fv .. ":" .. fv
        end
        table.sort(pieces)
        seen[value] = nil
        return result, "t{" .. table.concat(pieces,";") .. "}"
    end
    local modelkeys = {"object","macro","mode","container","isReadOnly","isplayerowned",
        "total","crewtotal","shoppinglisttotal","shoppinglistrefund","shoppinglist",
        "editingshoppinglist","upgradeplan","crew","errors","criticalerrors","warnings",
        "missingResources","validLicence","validLoadoutPossible","captainSelected",
        "settings","selectedPaintMod","customshipname","loadoutName","duration","objectgroup",
        "upgradetypeMode","repairplan","repairslots","damagedcomponents","totalrepairprice"}
    local function model(m)
        local raw = {}
        for _,k in ipairs(modelkeys) do raw[k] = m[k] end
        local out, fingerprint = copy(raw,{n=0,bytes=0},0,{})
        return out, fingerprint
    end
    local function container(m) return m.isReadOnly==true and "0" or id(m.container) end
    local function lifecycle(e,t,created)
        local ee,tt=stamp()
        assert(ee==e and tt>=t and (not created or tt-created<=30),"menu lifecycle changed during read")
        return tt
    end
    local function current(allow_readonly)
        local menus = d.menus()
        local m
        sequence(menus,256)
        for _,v in ipairs(menus) do
            assert(v.name == "ShipConfigurationMenu" or not v.shown, "another menu is open")
            if v.name == "ShipConfigurationMenu" then
                assert(m == nil, "ambiguous registered menu")
                m = v
            end
        end
        assert(m and m.shown == true and m.minimized == false, "menu not visibly ready")
        assert(m.mode == "purchase" or m.mode == "upgrade", "unsupported native menu mode")
        assert(allow_readonly or m.isReadOnly ~= true, "read-only mode not actionable")
        assert(type(m.param)=="table" and m.param[4]==m.mode,"menu parameter binding")
        if m.isReadOnly==true then
            assert(m.mode=="upgrade" and m.param[3]==nil and m.container==nil
                and sequence(m.param[5],1)==1 and id(d.convert_id(m.param[5][1]))==id(m.object)
                and id(m.object)~="0" and not m.objectgroup,"read-only ship binding")
        else
            assert(id(d.convert_id(m.param[3]))==id(m.container) and id(m.container)~="0","menu parameter binding")
        end
        assert(not m.contextMode or next(m.contextMode)==nil, "native modal context open")
        local f = m.infoFrame
        assert(type(f) == "table" and widget_id(f.id), "frame not built")
        assert(type(m.frames) == "table" and type(f.properties) == "table" and
            m.frames[f.properties.layer] == f.id, "frame not registered")
        assert(f.properties.panelMode==false, "unqualified panel-mode state")
        return m,f
    end
    local function locate(m,f,name)
        local spec = assert(specs[name],"unknown menu action")
        local found
        sequence(f.content,64)
        for _,tbl in ipairs(f.content) do
            if tbl.type == "table" then
                assert(widget_id(tbl.id), "table not built")
                sequence(tbl.rows,512)
                for ri,row in ipairs(tbl.rows) do
                    -- Native rows also carry properties/table metadata.
                    assert(type(row) == "table" and #row <= 32, "row overflow")
                    for k in pairs(row) do
                        if type(k)=="number" then
                            assert(k>=1 and k<=#row and k%1==0,"sparse native row")
                        end
                    end
                    for ci,cell in ipairs(row) do
                        if cell.type == spec.kind and cell.colspan ~= 0 then
                            local p,h = cell.properties,cell.handlers
                            assert(type(p)=="table" and type(h)=="table", "incomplete cell")
                            local handler = spec.kind=="button" and h.onClick or h.onDropDownConfirmed
                            local marker = spec.id and
                                (p.uiTriggerID == spec.id or
                                 ((name=="ship" or name=="class" or spec.help) and p.helpOverlayID == spec.id))
                            if (marker or (name=="loadout" and handler==m.dropdownLoadout)) then
                                assert(not found, "ambiguous current widget")
                                assert(type(handler)=="function", "missing native handler")
                                if spec.handler then assert(handler==m[spec.handler], "native handler mismatch") end
                                assert(widget_id(cell.id) and
                                    d.cell_id(tbl.id,ri,ci)==cell.id, "stale native widget")
                                assert(type(p.active)=="boolean", "unknown descriptor enabled state")
                                local native = d.widget_state(cell.id,spec.kind)
                                assert(type(native)=="table" and type(native.active)=="boolean", "unknown native enabled state")
                                assert(native.active==p.active, "native enabled state drift")
                                local maps = spec.kind=="button" and m.buttonScriptMap or m.dropdownScriptMap
                                local wanted = spec.kind=="button" and "onClick" or "onDropDownConfirmed"
                                local wrapper
                                sequence(maps,4096)
                                for _,w in ipairs(maps) do
                                    if w.layer==f.properties.layer and w.tableobj==tbl.id and
                                       w.row==ri and w.col==ci and w.type==wanted then
                                        assert(not wrapper and type(w.script)=="function", "ambiguous wrapper")
                                        if spec.kind=="button" then assert(w.button==cell.id,"wrapper button mismatch") end
                                        wrapper=w.script
                                    end
                                end
                                assert(wrapper,"missing built native wrapper")
                                found={cell=cell,table=tbl,row=ri,col=ci,handler=handler,
                                    wrapper=wrapper,enabled=native.active,kind=spec.kind,native=native,
                                    widget=cell.id,tableid=tbl.id}
                            end
                        end
                    end
                end
            end
        end
        assert(found, "native widget absent")
        return found
    end
    local function widget_model(w)
        local p=w.cell.properties
        local raw={enabled=w.enabled, widget=w.widget, hint=p.mouseOverText, start=p.startOption}
        if w.kind=="dropdown" then
            sequence(p.options,2048)
            sequence(w.native.options,2048)
            assert(#p.options==#w.native.options,"native option count drift")
            local unique={}
            for i,o in ipairs(p.options) do
                assert(type(o)=="table" and type(o.id)=="string" and text(o.id) and
                    o.id~="" and not unique[o.id], "invalid or duplicate native option")
                unique[o.id]=true
                local n=w.native.options[i]
                assert(n.id==o.id and type(n.active)=="boolean", "native option identity or state drift")
                assert(o.active==nil or o.active==n.active, "descriptor option state drift")
            end
            raw.options=w.native.options
            raw.descriptor_options=p.options
        end
        return copy(raw,{n=0,bytes=0},0,{})
    end
    local function refusal(err)
        return {status="REFUSED", reason=tostring(err), action_attempted=false}
    end
    function api.observe(ticket)
        local ok, out=pcall(function()
            local epoch,age=stamp()
            if ticket~=nil then
                local r=records[ticket]
                assert(r and r.action=="close" and r.status=="DISPATCHED_UNVERIFIED"
                    and epoch==r.epoch and age>=r.age,"owned close observation required")
                local seen=0
                local menus=d.menus();sequence(menus,256)
                for _,m in ipairs(menus) do
                    if m.name=="ShipConfigurationMenu" then
                        assert(m==r.menu,"registered menu replaced");seen=seen+1
                        -- Helper cleanup clears shown to nil (helper.lua1623).
                        assert(m.shown==nil or m.shown==false,"owned menu still shown or unknown")
                    end
                end
                assert(seen==1,"owned menu registration unavailable")
                local observed_shown=r.menu.shown
                lifecycle(epoch,age)
                assert(r.menu.shown==observed_shown and (observed_shown==nil or observed_shown==false),
                    "owned menu visibility changed during closing read")
                return {status="MENU_CLOSED_OBSERVED",action_attempted=true,economic_outcome_verified=false,
                    scope="OWNED_MENU_VISIBILITY_FLAG_ONLY",shown=false,shown_normalized=true,
                    native_shown_state=observed_shown==nil and "UNSET" or "FALSE",rendered_pixels_verified=false}
            end
            local m,f=current(true)
            local data=model(m)
            local widgets,repair_widgets={},{}
            for name in pairs(specs) do
                local wok,w=pcall(locate,m,f,name)
                if wok then
                    local vok,v=pcall(widget_model,w)
                    local target=(name=="repair_tab" or name=="repair_select") and repair_widgets or widgets
                    target[name]=vok and {status="OBSERVED",model=v} or
                        {status="UNKNOWN",reason=tostring(v)}
                else
                    local target=(name=="repair_tab" or name=="repair_select") and repair_widgets or widgets
                    target[name]={status="UNAVAILABLE",reason=tostring(w)}
                end
            end
            lifecycle(epoch,age)
            return {status="OBSERVED",epoch=epoch,age=age,container=container(m),
                mode=m.mode,frame=f.id,model=data,widgets=widgets,repair_widgets=repair_widgets,
                read_only=m.isReadOnly==true,
                scope="CURRENT_BUILT_CONFIGURATION_MODEL",rendered_pixels_verified=false,
                entry_route_verified=false,economic_outcome_verified=false,atomic=false}
        end)
        return ok and out or refusal(out)
    end
    local function store(r)
        assert(serial<MAX_TICKETS,"menu request limit")
        serial=serial+1
        r.ticket="menu-"..serial
        records[r.ticket]=r
        return {status="PREPARED",ticket=r.ticket,epoch=r.epoch,age=r.age,
            action=r.action,option=r.option,model=r.model,widget=r.widget_model,
            action_attempted=false,entry_route_verified=false,economic_outcome_verified=false}
    end
    function api.prepare(action,option,expected)
        local ok,out=pcall(function()
            if action=="close" then
                assert(option==nil and type(expected)=="table","close takes owned opening")
                local opened=records[expected.opening]
                assert(opened and opened.action=="open" and opened.status=="DISPATCHED_UNVERIFIED",
                    "successful owned opening required")
                local e,t=stamp();assert(e==opened.epoch,"opening session changed")
                local m,f=current(true)
                assert(m==opened.menu and m.mode==(opened.mode=="view" and "upgrade" or opened.mode)
                    and container(m)==opened.container,"opening subject rebound")
                if opened.ship then assert(id(m.object)==id(opened.ship),"opened ship changed") end
                assert((m.isReadOnly==true)==(opened.mode=="view") and m.contextMode==nil,"close context changed")
                local main=m.mainFrame
                assert(type(main)=="table" and widget_id(main.id) and type(main.properties)=="table"
                    and m.frames[main.properties.layer]==main.id and type(main.properties.standardButtons)=="table"
                    and main.properties.standardButtons.close==true,"native close not offered")
                assert(type(m.closeElement)=="function" and type(m.onCloseElement)=="function","native close handlers absent")
                local data,fp=model(m);lifecycle(e,t)
                return store{action=action,epoch=e,age=t,menu=m,frame=f,frameid=f.id,main=main,mainid=main.id,
                    close=m.closeElement,close_handler=m.onCloseElement,model=data,fingerprint=fp,
                    container=container(m),mode=m.mode}
            end
            assert(type(expected)=="table" and
                (expected.mode=="purchase" or expected.mode=="upgrade"),"expected context required")
            local e,t=stamp()
            local m,f=current()
            assert(expected.mode==m.mode and expected.container==id(m.container),"wrong menu subject")
            if action=="repair_tab" or action=="repair_select" then
                assert(option==nil and m.mode=="upgrade" and not m.objectgroup and expected.ship==id(m.object),
                    "single issued repair ship required")
                assert(sequence(m.damagedcomponents,2048)>0,"no observed damaged components")
                if action=="repair_tab" then assert(m.upgradetypeMode~="repair","repair tab already active")
                else
                    assert(m.upgradetypeMode=="repair" and sequence(m.repairslots,1)==1
                        and sequence(m.repairslots[1],1)==1 and id(m.repairslots[1][1][4])==expected.ship,
                        "repair slot subject mismatch")
                    assert(type(m.repairplan)=="table","repair plan missing")
                    for _,plan in pairs(m.repairplan) do assert(type(plan)=="table" and next(plan)==nil,
                        "repair selection would toggle existing plan") end
                end
            end
            local w=locate(m,f,action)
            assert(w.enabled,"native widget disabled")
            if w.kind=="dropdown" then
                assert(type(option)=="string","option required")
                local offered
                widget_model(w)
                for _,o in ipairs(w.native.options) do if o.id==option then offered=o end end
                assert(offered and offered.active==true,"option absent or disabled")
            else assert(option==nil,"button takes no option") end
            if action=="confirm" then
                assert(type(m.shoppinglist)=="table" and sequence(m.shoppinglist,1)==1 and
                    not m.editingshoppinglist and m.shoppinglist[1].amount==1 and
                    not m.shoppinglist[1].objectgroup,"only one native cart item supported")
            end
            local data,fingerprint=model(m)
            local wm,wf=widget_model(w)
            lifecycle(e,t)
            return store{action=action,option=option,epoch=e,age=t,menu=m,frame=f,
                frameid=f.id,widget=w,widget_model=wm,widget_fingerprint=wf,
                model=data,fingerprint=fingerprint,container=id(m.container),mode=m.mode}
        end)
        return ok and out or refusal(out)
    end
    function api.prepare_open(mode,station_token,ship_token)
        local ok,out=pcall(function()
            assert(mode=="purchase" or mode=="upgrade" or mode=="view","unsupported open mode")
            assert(mode~="purchase" or ship_token==nil,"purchase takes no ship")
            assert(mode~="view" or (station_token==nil and ship_token~=nil),"view takes one issued ship")
            local e,t=stamp()
            local menus=d.menus()
            sequence(menus,256)
            local target
            for _,m in ipairs(menus) do
                assert(not m.shown,"another menu is open")
                if m.name=="ShipConfigurationMenu" then assert(not target,"ambiguous registered menu");target=m end
            end
            assert(target,"registered configuration menu absent")
            local station
            if mode~="view" then station=d.resolve_issued(station_token,"station");assert(id(station)~="0","station absent") end
            local ship
            if ship_token~=nil then ship=d.resolve_issued(ship_token,"ship");assert(id(ship)~="0","ship absent") end
            if mode=="view" then assert(type(d.is_player_owned)=="function" and d.is_player_owned(ship)==true,"owned ship unavailable") end
            lifecycle(e,t)
            return store{action="open",mode=mode,epoch=e,age=t,station_token=station_token,
                ship_token=ship_token,station=station,ship=ship,container=station and id(station) or "0",menu=target}
        end)
        return ok and out or refusal(out)
    end
    function api.execute(ticket)
        local r=records[ticket]
        if not r then return refusal("unknown menu ticket") end
        if r.consumed then return {status=r.status or "UNCERTAIN",reason="ticket already consumed",
            action_attempted=r.attempted==true,ticket=ticket} end
        -- Consume even refused/stale attempts; neither exceptions nor lost replies retry.
        r.consumed=true
        local ok,err=pcall(function()
            local e,t=stamp()
            assert(e==r.epoch and t>=r.age and t-r.age<=30,"stale menu lifecycle")
            if r.action=="open" then
                local menus=d.menus()
                sequence(menus,256)
                local target
                for _,m in ipairs(menus) do
                    assert(not m.shown,"another menu is open")
                    if m.name=="ShipConfigurationMenu" then assert(not target,"ambiguous menu");target=m end
                end
                assert(target==r.menu,"registered menu changed")
                if r.station then assert(d.resolve_issued(r.station_token,"station")==r.station,"station identity changed") end
                if r.ship then assert(d.resolve_issued(r.ship_token,"ship")==r.ship,"ship identity changed") end
                local ships={}
                if r.ship then ships[1]=r.mode=="view" and tostring(r.ship) or r.ship end
                if r.mode=="view" then assert(type(d.is_player_owned)=="function" and d.is_player_owned(r.ship)==true,"ship ownership changed") end
                local params=d.convert_params({0,0,r.station,r.mode=="view" and "upgrade" or r.mode,ships})
                assert(type(params)=="table","native parameter conversion failed")
                lifecycle(e,t,r.age)
                r.attempted=true
                d.open_menu("ShipConfigurationMenu",params)
            elseif r.action=="close" then
                local m,f=current(true);local _,fp=model(m)
                assert(m==r.menu and f==r.frame and f.id==r.frameid and fp==r.fingerprint
                    and container(m)==r.container and m.mode==r.mode and m.contextMode==nil,"close view changed")
                assert(m.mainFrame==r.main and r.main.id==r.mainid
                    and m.frames[r.main.properties.layer]==r.mainid and r.main.properties.standardButtons.close==true
                    and m.closeElement==r.close and m.onCloseElement==r.close_handler,"close handler/frame changed")
                lifecycle(e,t,r.age);r.attempted=true;r.close(r.mainid,"close")
            else
                local m,f=current()
                assert(m==r.menu and f==r.frame and f.id==r.frameid and
                    id(m.container)==r.container and m.mode==r.mode,"menu rebuilt or rebound")
                local w=locate(m,f,r.action)
                assert(w.cell==r.widget.cell and w.table==r.widget.table and
                    w.widget==r.widget.widget and w.wrapper==r.widget.wrapper and
                    w.handler==r.widget.handler and w.enabled,"widget changed or disabled")
                local _,fp=model(m)
                local _,wf=widget_model(w)
                assert(fp==r.fingerprint and wf==r.widget_fingerprint,"native model changed")
                local _,loadout=copy(m.upgradeplan,{n=0,bytes=0},0,{})
                lifecycle(e,t,r.age)
                r.attempted=true
                if w.kind=="dropdown" then
                    d.set_dropdown_current(w.widget,r.option)
                    w.wrapper(w.widget,r.option)
                else w.wrapper() end
                if r.action=="repair_tab" or r.action=="repair_select" then
                    local _,after=copy(m.upgradeplan,{n=0,bytes=0},0,{})
                    assert(after==loadout,"repair UI changed loadout; outcome uncertain")
                end
            end
            lifecycle(e,t)
        end)
        r.status=ok and "DISPATCHED_UNVERIFIED" or (r.attempted and "UNCERTAIN" or "REFUSED")
        return {status=r.status,ticket=ticket,reason=ok and "native callback returned; outcome not proved" or tostring(err),
            action_attempted=r.attempted==true,economic_outcome_verified=false,
            entry_route_verified=false,automatic_retry=false}
    end
    function api.capacity()
        return {available=true,budgets={tickets={used=serial,limit=MAX_TICKETS}},
            cached_context='PER_REQUEST_NOT_EXPOSED',lifecycle_gate='OWNING_ASSEMBLY'}
    end
    return api
end

-- Native widget readback, copied from widget_fullscreen.lua's count/fill and
-- active checks. Only native named types are passed to native functions.
local function native_reader(ffi,C,g)
    local function declared(name,definition)
        if not pcall(ffi.typeof,name) then ffi.cdef(definition) end
    end
    local colorproof=ffi.typeof("struct { uint32_t red,green,blue,alpha; }")
    local optionproof=ffi.typeof([[struct {
        const char *id,*iconid,*text1,*text2,*text3,*text4,*mouseovertext,*font;
        struct { uint32_t red,green,blue,alpha; } overrideColor;
        bool displayRemoveOption,active,hasOverrideColor;
    }]])
    declared("Color","typedef struct { uint32_t red,green,blue,alpha; } Color;")
    declared("DropDownOption3",[[typedef struct {
        const char *id,*iconid,*text1,*text2,*text3,*text4,*mouseovertext,*font;
        Color overrideColor; bool displayRemoveOption,active,hasOverrideColor;
    } DropDownOption3;]])
    local function layout(a,b,fields)
        assert(ffi.sizeof(a)==ffi.sizeof(b) and ffi.alignof(a)==ffi.alignof(b),"native widget ABI size")
        for _,k in ipairs(fields) do assert(ffi.offsetof(a,k)==ffi.offsetof(b,k),"native widget ABI offset") end
    end
    layout("Color",colorproof,{"red","green","blue","alpha"})
    local color=ffi.new("Color")
    for _,k in ipairs({"red","green","blue","alpha"}) do
        color[k]=4294967295
        assert(color[k]==4294967295,"native Color unsigned width")
    end
    local strings={"id","iconid","text1","text2","text3","text4","mouseovertext","font"}
    local fields={"id","iconid","text1","text2","text3","text4","mouseovertext","font",
        "overrideColor","displayRemoveOption","active","hasOverrideColor"}
    layout("DropDownOption3",optionproof,fields)
    local probe=ffi.new("DropDownOption3")
    assert(ffi.istype("Color",probe.overrideColor),"native widget Color field type")
    for _,k in ipairs(strings) do assert(ffi.istype("const char *",probe[k]),"native widget string field type") end
    for _,k in ipairs({"displayRemoveOption","active","hasOverrideColor"}) do
        assert(type(probe[k])=="boolean","native widget bool field type")
    end
    ffi.cdef[[
        uint32_t GetNumDropDownOptions(const int dropdownid);
        uint32_t GetDropDownOptions3(DropDownOption3* result,uint32_t resultlen,const int dropdownid);
        bool IsDropDownActive(const int dropdownid);
        void SetDropDownCurOption(const int dropdownid,const char* optionid);
    ]]
    local function count(n)
        assert(type(n)=="number" and n>=0 and n<=2048 and n%1==0,"native option count overflow")
        return n
    end
    local bytes=0
    local function str(p)
        assert(p~=nil and p~=ffi.NULL,"native option string unknown")
        -- Native owns these valid C strings for the duration of this synchronous call.
        local s=ffi.string(p)
        assert(#s<=4096,"native option string overflow")
        bytes=bytes+#s
        assert(bytes<=131072,"native option payload overflow")
        return s
    end
    local function read(widget,kind)
        bytes=0
        assert(g.IsValidWidgetElement(widget)==true,"invalid native widget")
        if kind=="button" then
            local a=g.IsButtonActive(widget)
            assert(type(a)=="boolean","unknown native button state")
            return {active=a}
        end
        assert(kind=="dropdown","unsupported native widget")
        local a=C.IsDropDownActive(widget)
        assert(type(a)=="boolean","unknown native dropdown state")
        local n=count(C.GetNumDropDownOptions(widget))
        local out={}
        if n>0 then
            local buf=ffi.new("DropDownOption3[?]",n)
            assert(count(C.GetDropDownOptions3(buf,n,widget))==n,"native option count changed")
            for i=0,n-1 do
                local b=buf[i]
                out[i+1]={id=str(b.id),active=b.active,text=str(b.text1),
                    text2=str(b.text2),text3=str(b.text3),text4=str(b.text4),
                    hint=str(b.mouseovertext)}
            end
        end
        assert(count(C.GetNumDropDownOptions(widget))==n and
            C.IsDropDownActive(widget)==a and g.IsValidWidgetElement(widget)==true,
            "native widget changed during read")
        return {active=a,options=out,atomic=false}
    end
    return read, function(widget,option)
        assert(g.IsValidWidgetElement(widget)==true,"invalid native widget")
        C.SetDropDownCurOption(widget,option)
    end
end
return {new=new,native_reader=native_reader}

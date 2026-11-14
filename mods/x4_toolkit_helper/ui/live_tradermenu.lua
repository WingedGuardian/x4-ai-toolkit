-- BlueprintOrLicenceTraderMenu: built-widget routes plus one licence opener; no direct grant or payment.
-- Native menu_trader_blueprintsorlicences.lua126-145/275-281/325-340/
-- 348-460/560-577/595-678; Helper buttonScriptMap wrappers as live_menu.lua.
-- Opener mirrors md/npc_factionrepresentative.xml:272/295: the faction representative,
-- param {0,0,trader,'licence'}; the menu reads param[3]/[4] at lua126-139.
return function(deps)
    local d={}
    for _,k in ipairs({'menus','current','now','resolve_issued','component_id','valid_widget','cell_id','widget_state',
        'representative','convert_parameters','open_menu'}) do
        assert(type(deps[k])=='function','missing trader dependency: '..k);d[k]=deps[k]
    end
    local g={}
    for _,k in ipairs({'GetPlayerMoney','GetUIRelation','GetComponentData','GetContextByClass','GetOwnLicences','HasLicence','GetWareData'}) do
        assert(type(deps.globals[k])=='function','missing native '..k);g[k]=deps.globals[k]
    end
    local ffi,C=deps.ffi,deps.C
    assert(ffi and C,'native blueprint bindings required')
    if not pcall(ffi.typeof,'UIBlueprint') then ffi.cdef('typedef struct {const char *macro,*ware,*productionmethodid;} UIBlueprint;') end
    local proof=ffi.typeof('struct {const char *macro,*ware,*productionmethodid;}')
    assert(ffi.sizeof('UIBlueprint')==ffi.sizeof(proof) and ffi.alignof('UIBlueprint')==ffi.alignof(proof),'blueprint ABI size')
    local probe=ffi.new('UIBlueprint')
    for _,k in ipairs({'macro','ware','productionmethodid'}) do
        assert(ffi.offsetof('UIBlueprint',k)==ffi.offsetof(proof,k) and ffi.istype('const char *',probe[k]),'blueprint ABI field')
    end
    ffi.cdef[[uint32_t GetNumBlueprints(const char*,const char*,const char*);
        uint32_t GetBlueprints(UIBlueprint*,uint32_t,const char*,const char*,const char*);]]
    local api,records,serial,retired={}, {},0,false
    local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,a,b) return finite(v) and v%1==0 and v>=a and v<=b end
    local function text(v) assert(type(v)=='string' and #v>0 and #v<=256 and not v:find('[%c]'),'native identifier unavailable');return v end
    local function money(v) assert(finite(v) and v>=0 and v<90071992547409,'money unavailable/inexact');return v end
    local function boolean(v) assert(type(v)=='boolean','native boolean unavailable');return v end
    local function wid(v) return integer(v,1,2147483647) end
    local function id(v)
        if type(v)=='cdata' then v=tostring(v):match('^(%d+)ULL$') end
        if type(v)=='number' then assert(integer(v,1,9007199254740991),'inexact identity');v=string.format('%.0f',v) end
        assert(type(v)=='string' and v:match('^[1-9]%d*$') and #v<=20 and (#v<20 or v<='18446744073709551615'),'positive exact identity required');return v
    end
    local function seq(t,cap)
        assert(type(t)=='table' and #t<=cap,'bounded native list required')
        local n=0;for k in pairs(t) do assert(integer(k,1,#t),'sparse native list');n=n+1 end
        assert(n==#t,'sparse native list');return t
    end
    local function copy(v) if type(v)~='table' then return v end;local t={};for k,x in pairs(v) do t[k]=copy(x) end;return t end
    local function same(a,b)
        if type(a)~=type(b) then return false end;if type(a)~='table' then return a==b end
        for k,v in pairs(a) do if not same(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end;return true
    end
    local function stamp()
        assert(not retired,'trader adapter retired');local s=d.current();local out={}
        assert(type(s)=='table' and s.provider=='x4-live-session-v1','public session unavailable')
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do
            assert(type(s[k])=='string' and #s[k]>0 and #s[k]<=4096,'session field unavailable');out[k]=s[k]
        end
        local age=d.now();assert(finite(age) and age>=0,'elapsed clock unavailable');return out,age
    end
    local function blueprints()
        local n=C.GetNumBlueprints('','','');assert(integer(n,0,8192),'blueprint count unavailable/cap')
        local function read()
            local out={};if n==0 then return out end
            local a=ffi.new('UIBlueprint[?]',n)
            assert(C.GetBlueprints(a,n,'','','')==n,'blueprint count drift')
            for i=0,n-1 do
                assert(a[i].ware~=nil and a[i].ware~=ffi.NULL,'blueprint ware missing')
                local chars={}
                for j=0,256 do local x=a[i].ware[j];if x==0 then break end;assert(j<256,'blueprint identifier cap');chars[#chars+1]=string.char(x<0 and x+256 or x) end
                local ware=text(table.concat(chars));out[ware]=true -- production methods may repeat a ware
            end
            return out
        end
        local a=read();assert(same(a,read()) and C.GetNumBlueprints('','','')==n,'blueprint ownership changed');return a
    end
    local function licence(l)
        assert(type(l)=='table','licence metadata missing')
        local p=l.precursor
        assert(p==nil or type(p)=='string','licence precursor malformed')
        assert(finite(l.minrelation),'licence relation missing')
        return {id=text(l.id),type=text(l.type),price=money(l.price),minrelation=l.minrelation,
            precursor=p and text(p) or '',issellable=boolean(l.issellable)}
    end
    local function basket(m)
        local out,total,count={},0,0
        local chosen=m.mode=='licence' and m.licencestopurchase or m.blueprintstopurchase
        local other=m.mode=='licence' and m.blueprintstopurchase or m.licencestopurchase
        assert(type(chosen)=='table' and type(other)=='table' and next(other)==nil,'mixed/unknown basket')
        for k,v in pairs(chosen) do
            text(k);count=count+1;assert(count<=128,'basket cap')
            if m.mode=='licence' then assert(type(v)=='table','licence basket malformed');out[k]={type=text(v.type),price=money(v.price)}
            else out[k]={price=money(v)} end
            total=total+out[k].price;money(total)
        end
        assert(total==money(m.totalprice),'native basket total mismatch');return out,total,count
    end
    local function widget(m,f,t,row,ri,ci)
        local c=row[ci];assert(type(c)=='table' and c.type=='button' and c.colspan~=0 and wid(c.id)
            and d.valid_widget(c.id)==true and d.cell_id(t.id,ri,ci)==c.id,'button not built')
        assert(type(c.properties)=='table' and (type(c.properties.active)=='boolean' or type(c.properties.active)=='function')
            and type(c.handlers)=='table' and type(c.handlers.onClick)=='function','button descriptor unavailable')
        local state=d.widget_state(c.id);assert(type(state)=='table' and type(state.active)=='boolean','button state unavailable')
        if type(c.properties.active)=='boolean' then assert(c.properties.active==state.active,'button active drift') end
        local wrapper
        for _,w in ipairs(seq(m.buttonScriptMap,4096)) do
            if w.layer==f.properties.layer and w.tableobj==t.id and w.row==ri and w.col==ci and w.type=='onClick' then
                assert(not wrapper and w.button==c.id and type(w.script)=='function','ambiguous button wrapper');wrapper=w.script
            end
        end
        return {cell=c,row=row,table=t,id=c.id,tableid=t.id,index=ri,col=ci,handler=c.handlers.onClick,
            wrapper=assert(wrapper,'built wrapper absent'),active=state.active}
    end
    local function frame(token,mode)
        assert(mode=='licence' or mode=='blueprint','closed trader mode');text(token)
        local m
        for _,v in ipairs(seq(d.menus(),256)) do
            assert(type(v)=='table','menu registry malformed')
            if v.name=='BlueprintOrLicenceTraderMenu' then assert(not m,'duplicate trader menu');m=v
            else assert(not v.shown,'another menu is open') end
        end
        assert(m and m.shown==true and m.minimized==false and m.mode==mode,'requested trader not shown')
        assert(not m.queueupdate,'trader refresh pending')
        local trader=id(d.resolve_issued(token,'object'));assert(id(d.component_id(m.trader))==trader,'trader identity mismatch')
        local faction=text(g.GetComponentData(m.trader,'owner'));assert(faction==m.traderfaction,'trader faction changed')
        local container=id(d.component_id(g.GetContextByClass(m.trader,'container',false)))
        assert(container==id(d.component_id(m.tradecontainer)),'trader container changed')
        local f=m.frame;assert(type(f)=='table' and wid(f.id) and type(f.properties)=='table'
            and integer(f.properties.layer,1,4) and type(m.frames)=='table' and m.frames[f.properties.layer]==f.id
            and d.valid_widget(f.id)==true,'trader frame unbound')
        local funds=money(g.GetPlayerMoney());assert(funds==money(m.playermoney),'trader cached funds changed; refresh required')
        local chosen,total,count=basket(m)
        local out={trader=trader,faction=faction,container=container,mode=mode,funds=funds,basket=chosen,total=total,basket_count=count,rows={}}
        local owned=mode=='blueprint' and blueprints() or nil
        local licences={}
        if mode=='licence' then
            out.relation=g.GetUIRelation(faction);assert(finite(out.relation) and out.relation==m.uirelation,'cached relation changed')
            for _,l in ipairs(seq(g.GetOwnLicences(faction),512)) do local p=licence(l);assert(not licences[p.id],'duplicate licence');licences[p.id]=p end
        end
        local prices={};local nodes=0
        local function collect(t,depth)
            assert(type(t)=='table' and depth<=3,'blueprint price table malformed')
            for k,v in pairs(t) do
                text(k);nodes=nodes+1;assert(nodes<=8192,'price catalogue cap')
                if type(v)=='table' then collect(v,depth+1)
                else assert(not prices[k],'ambiguous blueprint price');prices[k]=money(v) end
            end
        end
        if mode=='blueprint' then collect(m.table_wares,1) end
        local refs,seen={},{}
        local topfound=false
        for _,t in ipairs(seq(f.content,64)) do
            if t.type=='table' then
                assert(wid(t.id) and d.valid_widget(t.id)==true,'table not built')
                if t==m.table_top then topfound=true end
                for ri,row in ipairs(seq(t.rows,1024)) do
                    assert(row.index==ri,'native row index drift')
                    local raw=row.rowdata
                    if t==m.table_top and type(raw)=='table' and (raw[1]=='entry' or raw[1]=='category') then
                        assert(#seq(raw,2)==2,'row identity shape');local key=text(raw[2]);local kind=raw[1]
                        assert(not seen[kind..':'..key] and #out.rows<512,'duplicate/overflow built rows');seen[kind..':'..key]=true
                        local r={kind=kind,key=key};local col
                        if kind=='category' then
                            assert(mode=='blueprint','licence category unexpected');assert(type(m.expanded)=='table','expansion state unavailable')
                            r.expanded=m.expanded[key]==true;assert(m.expanded[key]==nil or type(m.expanded[key])=='boolean','expansion state malformed')
                            for ci=1,2 do if type(row[ci])=='table' and row[ci].type=='button' then assert(not col,'ambiguous category button');col=ci end end
                        elseif mode=='licence' then
                            local l=assert(licences[key],'licence not in native catalogue');assert(l.issellable,'licence not sellable')
                            local old
                            for _,v in ipairs(seq(m.licences,512)) do if v.id==key then assert(not old,'duplicate cached licence');old=licence(v) end end
                            assert(same(l,old),'licence quote metadata changed');r.quote=l;r.price=l.price
                            r.owned=boolean(g.HasLicence('player',l.type,faction))
                            r.precursor_owned=l.precursor=='' or boolean(g.HasLicence('player',l.precursor,faction))
                            r.eligible=not r.owned and r.precursor_owned and out.relation>=l.minrelation;col=5
                        else
                            r.price=assert(prices[key],'blueprint quote missing');r.owned=owned[key]==true
                            assert(type(m.blueprintlist)=='table' and (m.blueprintlist[key]==true)==r.owned,'cached blueprint ownership changed')
                            local required,hidden=g.GetWareData(key,'tradelicence','ishiddenwithoutlicence')
                            assert(type(required)=='string' and #required<=256,'ware licence unavailable');boolean(hidden)
                            r.required_licence=required;r.licenced=boolean(g.HasLicence('player',required,faction))
                            assert(not hidden or r.licenced,'hidden blueprint row stale');r.eligible=not r.owned and r.licenced;col=5
                        end
                        r.selected=chosen[key]~=nil
                        if kind=='entry' and r.owned then
                            assert(type(row[5])=='table' and row[5].type=='text','owned row is not text');r.active=false
                        else
                            local w=widget(m,f,t,row,ri,assert(col,'category button absent'));refs[kind..':'..key]=w;r.active=w.active
                            local tip=w.cell.properties.mouseOverText
                            r.disabled_reason=type(tip)=='string' and #tip<=4096 and tip or 'UNKNOWN'
                        end
                        out.rows[#out.rows+1]=r
                    elseif raw=='buttonrow' then
                        assert(not refs.confirm,'duplicate confirm row');refs.confirm=widget(m,f,t,row,ri,1)
                        assert(refs.confirm.cell.properties.helpOverlayID=='trader_purchase_confirm','wrong confirm control')
                    end
                end
            end
        end
        assert(topfound and refs.confirm,'trader table or confirm missing');out.confirm_active=refs.confirm.active
        return out,{menu=m,frame=f,frameid=f.id,refs=refs}
    end
    local OPEN='BlueprintOrLicenceTraderMenu'
    -- Vanilla opens this menu only from the representative conversation (npc_factionrepresentative.xml:272,
    -- choice param {0,0,$FactionRepresentative,'licence'}; opened at :295). Mirrored here: licence mode,
    -- the trader is its owner faction's representative, the trader menu is registered and closed, and no
    -- other menu is shown. Not mirrorable from UI Lua: the faction's nolicencesale tag and the diplomat route.
    local function opening(token,mode)
        assert(mode=='licence','closed opener mode: licence only');text(token)
        local m
        for _,v in ipairs(seq(d.menus(),256)) do
            assert(type(v)=='table','menu registry malformed')
            if v.name==OPEN then assert(not m,'duplicate trader menu');m=v
            else assert(not v.shown,'another menu is open') end
        end
        assert(m,'trader menu not registered');assert(not m.shown,'trader menu already open')
        local raw=d.resolve_issued(token,'object');local trader=id(d.component_id(raw))
        local faction=text(g.GetComponentData(d.component_id(raw),'owner'))
        assert(id(d.component_id(d.representative(faction)))==trader,'trader is not its faction representative')
        return {menu=m,raw=raw,trader=trader,faction=faction}
    end
    local function fail(reason,attempted) return {status=attempted and 'UNCERTAIN' or 'REFUSED',reason=tostring(reason):sub(1,2048),action_attempted=attempted==true,purchase_verified=false} end
    function api.prepare_open(token,mode)
        local ok,out=pcall(function()
            assert(serial<128,'trader preparation capacity');local s,age=stamp();local o=opening(token,mode)
            local after,ended=stamp();assert(same(s,after) and ended>=age,'prepare lifecycle changed')
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={token=token,mode=mode,kind='open',key=OPEN,value=mode,open=o,stamp=s,age=ended,used=false,attempted=false}
            return {status='PREPARED',ticket=ticket,trader=o.trader,faction=o.faction,mode=mode,
                action_attempted=false,purchase_verified=false,menu_open_verified=false}
        end);return ok and out or fail(out,false)
    end
    function api.observe(token,mode,start)
        local ok,out=pcall(function()
            assert(integer(start,1,513),'bounded one-based row offset required');local s,age=stamp();local model=frame(token,mode)
            assert(start<=#model.rows+1,'row offset beyond built frame');local rows=model.rows;model.rows={}
            for i=start,math.min(#rows,start+15) do model.rows[#model.rows+1]=rows[i] end
            local after,ended=stamp();assert(same(s,after) and ended>=age,'observation lifecycle changed')
            model.status='OBSERVED';model.stamp=s;model.start_time=age;model.end_time=ended;model.observer_clock='UI_ELAPSED'
            model.built_row_count=#rows;model.start_index=start;model.returned_count=#model.rows;model.next_index=start+#model.rows<=#rows and start+#model.rows or 0
            model.scope='CURRENT_BUILT_TRADER_ROWS';model.full_catalogue=false;model.atomic=false;model.rendered_pixels_verified=false;model.purchase_verified=false
            return model
        end);return ok and out or fail(out,false)
    end
    local function prepare(token,mode,kind,key,value)
        local ok,out=pcall(function()
            text(key);assert(serial<128,'trader preparation capacity');local s,age=stamp();local model,refs=frame(token,mode)
            local row
            for _,r in ipairs(model.rows) do if r.key==key and r.kind==(kind=='expand' and 'category' or 'entry') then row=r end end
            assert(row,'requested item/category not built');local w
            if kind=='expand' then
                assert(type(value)=='boolean' and row.expanded~=value,'desired category state already present');w=refs.refs['category:'..key]
            else
                money(value);assert(value>0 and row.price==value and row.eligible,'current item quote/refusal mismatch')
                if kind=='select' then assert(model.basket_count==0 and not row.selected and model.funds>value,'selection requires empty affordable basket');w=refs.refs['entry:'..key]
                else
                    assert(model.basket_count==1 and model.basket[key] and model.total==value and model.funds>=value,'confirmation basket mismatch')
                    if mode=='licence' then assert(model.basket[key].type==row.quote.type,'basket licence type mismatch') end
                    w=refs.refs.confirm
                end
            end
            assert(w and w.active,'native button disabled');local after,ended=stamp();assert(same(s,after) and ended>=age,'prepare lifecycle changed')
            serial=serial+1;local ticket=tostring(serial)
            records[ticket]={token=token,mode=mode,kind=kind,key=key,value=value,model=model,refs=refs,widget=w,stamp=s,age=ended,used=false,attempted=false}
            return {status='PREPARED',ticket=ticket,before={trader=model.trader,mode=model.mode,faction=model.faction,
                funds=model.funds,total=model.total,basket_count=model.basket_count,item=copy(row)},action_attempted=false,purchase_verified=false}
        end);return ok and out or fail(out,false)
    end
    function api.prepare_expand(token,mode,key,desired) return prepare(token,mode,'expand',key,desired) end
    function api.prepare_select(token,mode,key,price) return prepare(token,mode,'select',key,price) end
    function api.prepare_confirm(token,mode,key,price) return prepare(token,mode,'confirm',key,price) end
    function api.execute(ticket)
        local r=records[ticket];if not r then return fail('unknown private trader ticket',false) end
        if r.used then return {status=r.status or 'UNCERTAIN',action_attempted=r.attempted,reason='ticket consumed',purchase_verified=false} end
        r.used=true
        local ok,why=pcall(function()
            local s,age=stamp();assert(same(s,r.stamp) and age>=r.age and age-r.age<=5,'stale trader lifetime')
            if r.kind=='open' then
                local o=opening(r.token,r.mode)
                assert(o.menu==r.open.menu and o.trader==r.open.trader and o.faction==r.open.faction,'trader opener rebound')
                local p=d.convert_parameters({0,0,d.component_id(o.raw),'licence'})
                assert(type(p)=='table' and p[1]==0 and p[2]==0 and p[4]=='licence','native parameter conversion unavailable')
                local final,ended=stamp()
                assert(same(s,final) and ended>=age and ended-r.age<=5,'lifecycle changed or ticket expired before dispatch')
                r.attempted=true;d.open_menu(OPEN,p)
                local after,finished=stamp();assert(same(final,after) and finished>=ended,'post-dispatch lifecycle unavailable')
                return
            end
            local model,refs=frame(r.token,r.mode);assert(same(model,r.model),'trader quote, basket or eligibility changed')
            assert(refs.menu==r.refs.menu and refs.frame==r.refs.frame and refs.frameid==r.refs.frameid,'trader frame rebound')
            local w=refs.refs[r.kind=='confirm' and 'confirm' or (r.kind=='expand' and 'category:' or 'entry:')..r.key]
            for _,k in ipairs({'cell','row','table','id','tableid','index','col','handler','wrapper'}) do assert(w and w[k]==r.widget[k],'trader widget rebound') end
            assert(w.active,'native button disabled');local final,ended=stamp()
            assert(same(s,final) and ended>=age and ended-r.age<=5,'lifecycle changed or ticket expired before dispatch')
            r.attempted=true;w.wrapper()
            local after,finished=stamp();assert(same(final,after) and finished>=ended,'post-dispatch lifecycle unavailable')
        end)
        r.status=ok and 'DISPATCHED_UNVERIFIED' or r.attempted and 'UNCERTAIN' or 'REFUSED'
        local out={status=r.status,action_attempted=r.attempted,kind=r.kind,item=r.key,price_or_expanded=r.value,
            reason=ok and 'native built wrapper returned; resulting basket/charge/ownership require independent readback' or tostring(why):sub(1,2048),purchase_verified=false}
        if r.kind=='open' then
            out.menu_open_verified=false
            if ok then out.reason='native OpenMenu returned; a later observe must find the trader menu shown' end
        end
        return out
    end
    function api.dispose() retired=true;return true end
    return api
end

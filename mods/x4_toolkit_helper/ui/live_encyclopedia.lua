-- Read-only built EncyclopediaMenu descriptors, never arbitrary text callbacks.
-- Native encyclopedia1478-1526/1609-1704/1928-1930; Helper3888/4151/5475-5520.
return function(deps)
    local d={}
    for _,k in ipairs({'menus','current','now','valid_widget','cell_id','encode'}) do
        assert(type(deps[k])=='function','missing encyclopedia dependency: '..k);d[k]=deps[k]
    end
    local api,retired={},false
    local roles={index={field='table_index',columns=6},description={field='table_description',columns=1},detail={field='table_detail',columns=5}}
    local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,a,b) return finite(v) and v%1==0 and v>=a and v<=b end
    local function wid(v) return integer(v,1,2147483647) end
    local function seq(t,cap)
        assert(type(t)=='table' and #t<=cap,'bounded native list required')
        local n=0;for k in pairs(t) do assert(integer(k,1,#t),'sparse native list');n=n+1 end
        assert(n==#t,'sparse native list');return t
    end
    local function same(a,b)
        if type(a)~=type(b) then return false end;if type(a)~='table' then return a==b end
        for k,v in pairs(a) do if not same(v,b[k]) then return false end end
        for k in pairs(b) do if a[k]==nil then return false end end;return true
    end
    local function scalar(v)
        local t=type(v)
        if v==nil then return {kind='ABSENT'} end
        if t=='string' then assert(#v<=4096 and not v:find('%z'),'context string cap/NUL');return {kind='STRING',value=v} end
        if t=='boolean' then return {kind='BOOLEAN',value=v} end
        if t=='number' and finite(v) then return {kind='NUMBER',value=v} end
        if t=='cdata' then
            local n=tostring(v):match('^(%d+)ULL$')
            if n and #n<=20 and (#n<20 or n<='18446744073709551615') then return {kind='RAW_UINT64',value=n} end
        end
        return {kind='UNKNOWN',lua_type=t}
    end
    local function stamp()
        assert(not retired,'encyclopedia observer retired');local s=d.current();local out={}
        assert(type(s)=='table' and s.provider=='x4-live-session-v1','public identity unavailable')
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do
            assert(type(s[k])=='string' and #s[k]>0 and #s[k]<=4096,'public field missing');out[k]=s[k]
        end
        local age=d.now();assert(finite(age) and age>=0,'UI elapsed clock unavailable');return out,age
    end
    local function snapshot(section)
        local role=assert(roles[section],'closed encyclopedia section required');local m
        for _,v in ipairs(seq(d.menus(),256)) do
            assert(type(v)=='table','native menu registry malformed')
            if v.name=='EncyclopediaMenu' then assert(not m,'duplicate EncyclopediaMenu');m=v
            else assert(not v.shown,'another menu is shown') end
        end
        assert(m and m.shown==true and m.minimized==false and m.encyclopediaMode=='encyclopedia','encyclopedia not shown')
        assert(m.displayRunning==nil and not m.refresh,'encyclopedia display/refresh pending')
        local f=m.frame
        assert(type(f)=='table' and wid(f.id) and type(f.properties)=='table' and integer(f.properties.layer,1,4)
            and type(m.frames)=='table' and m.frames[f.properties.layer]==f.id and d.valid_widget(f.id)==true,'frame not bound')
        local tid=m[role.field];assert(wid(tid),'section table not published');local t
        for _,v in ipairs(seq(f.content,64)) do
            if v.type=='table' and v.id==tid then assert(not t,'duplicate section table');t=v end
        end
        assert(t and t.numcolumns==role.columns and d.valid_widget(tid)==true,'section table not built/shape mismatch')
        local context={}
        for _,k in ipairs({'mode','library','id','object'}) do context[k]=scalar(m[k]) end
        local out={section=section,frame=f.id,table_id=tid,context=context,cells={},rows=#seq(t.rows,2048),
            text_cells=0,static_cells=0,unknown_cells=0,nontext_cells=0,spanned_cells=0,total_cell_slots=0}
        local refs={{ref=m},{ref=f},{ref=t}};local identities={}
        for _,k in ipairs({'mode','library','id','object'}) do refs[#refs+1]={ref=m[k]} end
        for ri,row in ipairs(t.rows) do
            assert(type(row)=='table' and row.index==ri,'row index drift')
            refs[#refs+1]={ref=row}
            -- Native rows also carry metadata keys; numeric keys must still be dense and bounded.
            for k in pairs(row) do if type(k)=='number' then assert(integer(k,1,role.columns),'extra/sparse row cell') end end
            for ci=1,role.columns do
                local c=row[ci]
                assert(type(c)=='table' and type(c.type)=='string' and #c.type>0 and #c.type<=64
                    and integer(c.colspan,0,role.columns),'missing/malformed native cell')
                out.total_cell_slots=out.total_cell_slots+1
                if c.colspan==0 then out.spanned_cells=out.spanned_cells+1
                elseif c.type=='text' then
                    assert(wid(c.id) and not identities[c.id] and d.valid_widget(c.id)==true
                        and d.cell_id(tid,ri,ci)==c.id,'text widget missing/duplicate/stale');identities[c.id]=true
                    assert(type(c.properties)=='table','text properties missing')
                    local v=c.properties.text;local item={row=ri,column=ci,widget=c.id,text_kind=type(v)}
                    if type(v)=='string' then
                        assert(#v<=4096 and not v:find('%z'),'text byte cap/NUL');item.status='OBSERVED';item.text=v;out.static_cells=out.static_cells+1
                    else
                        item.status='UNKNOWN';item.reason=type(v)=='function' and 'DYNAMIC_CALLBACK_NOT_INVOKED' or 'NONSTRING_DESCRIPTOR'
                        out.unknown_cells=out.unknown_cells+1
                    end
                    out.cells[#out.cells+1]=item;out.text_cells=out.text_cells+1;assert(out.text_cells<=4096,'text cell cap')
                    refs[#refs+1]={ref=c};refs[#refs+1]={ref=c.properties}
                    -- Keep callback/value identity private; never serialize or invoke it.
                    refs[#refs+1]={ref=v}
                else out.nontext_cells=out.nontext_cells+1 end
            end
        end
        return out,refs
    end
    local function refs_equal(a,b)
        if #a~=#b then return false end
        for i=1,#a do
            if a[i].ref~=b[i].ref then return false end
        end
        return true
    end
    function api.read(section,start)
        local ok,out=pcall(function()
            assert(integer(start,1,4097),'bounded one-based text offset required');local before,began=stamp()
            local a,ar=snapshot(section);local b,br=snapshot(section)
            assert(same(a,b) and refs_equal(ar,br),'built text/context/widget changed between reads')
            local after,ended=stamp();assert(same(before,after) and ended>=began,'read lifecycle/clock changed')
            assert(start<=a.text_cells+1,'offset beyond selected table');local all=a.cells;a.cells={}
            for i=start,math.min(#all,start+3) do a.cells[#a.cells+1]=all[i] end
            a.status='OBSERVED';a.scope='BUILT_ENCYCLOPEDIA_TEXT_DESCRIPTOR_WINDOW';a.stamp=before
            a.start_time=began;a.end_time=ended;a.observer_clock='UI_ELAPSED';a.start_index=start;a.returned_count=#a.cells
            a.next_index=start+#a.cells<=a.text_cells and start+#a.cells or 0
            a.stability='TWO_EQUAL_DESCRIPTOR_PROJECTIONS_WITH_SAME_NATIVE_IDENTITIES'
            a.all_text_descriptors_known=a.unknown_cells==0;a.covers_selected_table=start==1 and #a.cells==a.text_cells
            a.atomic=false;a.rendered_pixels_verified=false;a.full_encyclopedia_coverage=false;a.property_pane_covered=false
            local encoded=d.encode(a);assert(type(encoded)=='string' and #encoded<=30000,'encoded text window cap')
            return a
        end)
        return ok and out or {status='UNAVAILABLE',scope='BUILT_ENCYCLOPEDIA_TEXT_DESCRIPTOR_WINDOW',reason=tostring(out):sub(1,2048),rendered_pixels_verified=false}
    end
    function api.dispose() retired=true;return true end
    return api
end

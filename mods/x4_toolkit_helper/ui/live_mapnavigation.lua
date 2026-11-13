-- Fixed native map information route. DockedMenu1494 supplies infomode params;
-- MapMenu6330 retains a valid subtab, 10549 builds objectinfo tab buttons,
-- 19853/19920 build panel buttons. Invoke their registered Helper wrappers;
-- never assign panel/info state or call gameplay/actor functions directly.
return function(d)
    local dep={}
    for _,k in ipairs({'menus','public_current','clock','resolve_issued','component_id','valid_widget',
        'cell_id','widget_state','open_menu','convert_parameters'}) do
        assert(type(d[k])=='function','missing map-navigation dependency: '..k);dep[k]=d[k]
    end
    local api,records,count,retired={}, {},0,false
    local function finite(v) return type(v)=='number' and v==v and math.abs(v)<math.huge end
    local function integer(v,a,b) return finite(v) and v%1==0 and v>=a and v<=b end
    local function wid(v) return integer(v,1,2147483647) end
    local function id(v)
        if type(v)=='cdata' then v=tostring(v):match('^(%d+)ULL$') end
        if type(v)=='number' then assert(integer(v,1,9007199254740991),'inexact identity');v=string.format('%.0f',v) end
        assert(type(v)=='string' and v:match('^[1-9]%d*$') and #v<=20 and
            (#v<20 or v<='18446744073709551615'),'exact component identity required');return v
    end
    local function seq(t,cap)
        assert(type(t)=='table' and #t<=cap,'bounded list required');local n=0
        for k in pairs(t) do assert(integer(k,1,#t),'sparse list');n=n+1 end
        assert(n==#t,'sparse list');return t
    end
    local function same(a,b) for k,v in pairs(a) do if b[k]~=v then return false end end;return true end
    local function stamp()
        assert(not retired,'map navigation retired');local s=dep.public_current();local out={}
        assert(type(s)=='table' and s.provider=='x4-live-session-v1','public identity unavailable')
        for _,k in ipairs({'provider','revision','session','generation','world_token'}) do
            assert(type(s[k])=='string' and #s[k]>0 and #s[k]<=4096,'public stamp unavailable');out[k]=s[k]
        end
        local age=dep.clock();assert(finite(age) and age>=0,'clock unavailable');return out,age
    end
    local function registry(closed)
        local m
        for _,v in ipairs(seq(dep.menus(),256)) do
            assert(type(v)=='table','malformed registered menu')
            if v.name=='MapMenu' then assert(not m,'duplicate MapMenu');m=v
            else assert(not v.shown,'other menu open') end
        end
        assert(m,'MapMenu unavailable')
        if closed then assert(not m.shown,'map already open')
        else
            assert(m.shown==true and m.minimized==false and m.mode==nil and m.contextMenuMode==nil
                and not m.showMultiverse and not m.createMainFrameRunning,'unsupported map state')
        end
        return m
    end
    local function pane(m,side,target)
        assert(side=='left' or side=='right','explicit info pane required')
        assert(m[side=='left' and 'infoTableMode' or 'searchTableMode']=='info','pane is not information')
        assert(id(dep.component_id(m.infoSubmenuObject))==target,'displayed context differs')
        assert(type(m.infoMode)=='table' and type(m.infoMode[side])=='string','info submode unavailable')
        assert(type(m.panelState)=='table' and type(m.panelState[side..'menu'])=='boolean','panel visibility unknown')
        assert(not m[side=='left' and 'createInfoFrameRunning' or 'createInfoFrame2Running'],'info frame rebuilding')
    end
    local function button(m,side,step,target)
        pane(m,side,target)
        local layer,f,tableid,desc,rowkey,overlay
        if step=='panel' then
            assert(m.panelMode==true and m.panelState[side..'menu']==false and m.panelState[side..'bar']==true,'panel button not applicable')
            layer=6;f=m.mainFrame;tableid=m[side=='left' and 'sideBar' or 'rightBar'];rowkey='info'
            overlay=side=='left' and 'map_sidebar_information' or 'map_sidebar_information2'
        else
            assert(step=='objectinfo' and (not m.panelMode or m.panelState[side..'menu']==true),'info pane is hidden')
            layer=side=='left' and 4 or 5;f=m[side=='left' and 'infoFrame' or 'infoFrame2']
            desc=m[side=='left' and 'orderHeaderTable' or 'orderHeaderTableRight']
            assert(type(desc)=='table','native tab header unavailable');tableid=desc.id
            rowkey='orders_tabs';overlay='mapst_ao_information'
        end
        assert(type(f)=='table' and wid(f.id) and type(f.properties)=='table' and f.properties.layer==layer
            and type(m.frames)=='table' and m.frames[layer]==f.id and dep.valid_widget(f.id)==true,'frame unbound')
        local t
        for _,v in ipairs(seq(f.content,64)) do
            if v.type=='table' and v.id==tableid then assert(not t,'duplicate table');t=v end
        end
        assert(t and wid(t.id) and dep.valid_widget(t.id)==true and (not desc or desc==t),'native table unbound')
        local found
        for ri,row in ipairs(seq(t.rows,512)) do
            if row.rowdata==rowkey then
                assert(row.index==ri and #row<=64,'unbound navigation row')
                for ci,c in ipairs(row) do
                    if type(c)=='table' and c.type=='button' and type(c.properties)=='table' and c.properties.helpOverlayID==overlay then
                        assert(not found and c.colspan~=0 and wid(c.id) and dep.cell_id(t.id,ri,ci)==c.id
                            and dep.valid_widget(c.id)==true,'ambiguous/stale navigation widget')
                        assert(type(c.properties.active)=='boolean' and type(c.handlers)=='table'
                            and type(c.handlers.onClick)=='function','native button descriptor unavailable')
                        local state=dep.widget_state(c.id)
                        assert(type(state)=='table' and type(state.active)=='boolean' and state.active==c.properties.active,'native active-state drift')
                        local wrapper
                        for _,w in ipairs(seq(m.buttonScriptMap,4096)) do
                            if w.layer==layer and w.tableobj==t.id and w.row==ri and w.col==ci and w.type=='onClick' then
                                assert(not wrapper and w.button==c.id and type(w.script)=='function','ambiguous native wrapper');wrapper=w.script
                            end
                        end
                        assert(wrapper,'registered navigation wrapper unavailable')
                        found={cell=c,row=row,table=t,frame=f,handler=c.handlers.onClick,wrapper=wrapper,
                            frameid=f.id,tableid=t.id,widget=c.id,ri=ri,ci=ci,enabled=state.active,mode=m.infoMode[side]}
                    end
                end
            end
        end
        return assert(found,'navigation button absent')
    end
    local function refused(why) return {status='REFUSED',reason=tostring(why),action_attempted=false} end
    function api.observe(context,side)
        local ok,out=pcall(function()
            local before,age=stamp();local target=id(dep.resolve_issued(context,'object'));local m=registry(false);pane(m,side,target)
            local after,ended=stamp();assert(same(before,after) and ended>=age,'observation lifecycle changed')
            return {status='OBSERVED',context=target,side=side,submode=m.infoMode[side],panel_mode=m.panelMode==true,
                panel_open=m.panelState[side..'menu'],scope='NATIVE_MAP_INFO_STATE',rendered_pixels_verified=false,atomic=false}
        end);return ok and out or refused(out)
    end
    function api.prepare(context,side,step)
        local ok,out=pcall(function()
            assert(count<128 and (step=='open' or step=='panel' or step=='objectinfo'),'closed navigation step/capacity')
            assert(side=='left' or side=='right','explicit pane required')
            local before,age=stamp();local target=id(dep.resolve_issued(context,'object'))
            local m=registry(step=='open');local w
            if step=='open' then assert(side=='left','native infomode opens left pane') else w=button(m,side,step,target);assert(w.enabled,'native navigation disabled') end
            local after,ended=stamp();assert(same(before,after) and ended>=age,'preparation lifecycle changed')
            count=count+1;local ticket=tostring(count)
            records[ticket]={stamp=before,age=ended,context=context,target=target,side=side,step=step,menu=m,widget=w,consumed=false,attempted=false}
            return {status='PREPARED',ticket=ticket,context=target,side=side,step=step,action_attempted=false}
        end);return ok and out or refused(out)
    end
    function api.execute(ticket)
        local r=records[ticket];if not r then return refused('unknown navigation ticket') end
        if r.consumed then return {status=r.status or 'UNCERTAIN',action_attempted=r.attempted,reason='ticket consumed'} end
        r.consumed=true
        local ok,why=pcall(function()
            local before,age=stamp();assert(same(before,r.stamp) and age>=r.age and age-r.age<=30,'stale navigation lifetime')
            local target=dep.resolve_issued(r.context,'object');assert(id(target)==r.target,'issued context changed')
            local m=registry(r.step=='open');assert(m==r.menu,'native menu replaced');local invoke
            if r.step=='open' then
                -- Same native seven-position parameter structure as DockedMenu.
                local params=dep.convert_parameters({0,0,true,nil,nil,'infomode',{'info',target}})
                assert(type(params)=='table','native parameter conversion failed')
                invoke=function() dep.open_menu('MapMenu',params) end
            else
                local w=button(m,r.side,r.step,r.target)
                assert(same(r.widget,w) and w.enabled,'navigation widget/model changed')
                invoke=w.wrapper
            end
            local final,ended=stamp();assert(same(before,final) and ended>=age,'pre-dispatch lifecycle changed')
            r.attempted=true;invoke()
            local after,finished=stamp();assert(same(final,after) and finished>=ended,'post-dispatch lifecycle unavailable')
        end)
        r.status=ok and 'DISPATCHED_UNVERIFIED' or r.attempted and 'UNCERTAIN' or 'REFUSED'
        return {status=r.status,action_attempted=r.attempted,context=r.target,side=r.side,step=r.step,
            reason=ok and 'native route dispatched; inspect next UI model separately' or tostring(why),
            info_open_verified=false,actor_verified=false,rendered_pixels_verified=false}
    end
    function api.dispose() retired=true;return true end
    return api
end

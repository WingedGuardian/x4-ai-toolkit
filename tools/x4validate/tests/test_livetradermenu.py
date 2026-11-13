"""Actual Lua adapter, synthetic native menu/widgets; no game calls."""
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE = Path(__file__).resolve().parents[3] / 'mods/x4_toolkit_helper/ui/live_tradermenu.lua'


def make(mode='licence'):
    r = LuaRuntime(unpack_returned_tuples=True)
    r.execute('''
        ffi=require('ffi');age=10;funds=1000;writes=0;queries=0;relation=20;owned=false;precursor=true
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        function native() queries=queries+1 end
        licence={id='lic',type='lic_type',price=100,minrelation=10,precursor='precursor',issellable=true}
        cached={id='lic',type='lic_type',price=100,minrelation=10,precursor='precursor',issellable=true}
        C={GetNumBlueprints=function()native();return bpcount or 0 end,
            GetBlueprints=function(a,n)native();for i=0,n-1 do a[i].ware=bpware or 'ware' end;return n end}
        g={GetPlayerMoney=function()native();return funds end,
            GetUIRelation=function()native();return relation end,
            GetComponentData=function(o,k)native();assert(o==42ULL and k=='owner');return faction or 'argon' end,
            GetContextByClass=function(o,k,v)native();assert(o==42ULL and k=='container' and v==false);return 44ULL end,
            GetOwnLicences=function(f)native();assert(f=='argon');return {licence} end,
            HasLicence=function(p,k,f)native();assert(p=='player' and f=='argon');if k=='lic_type' then return owned end;return precursor end,
            GetWareData=function(k,a,b)native();assert(k=='ware' and a=='tradelicence' and b=='ishiddenwithoutlicence');if missingware then return nil,false end;return 'precursor',false end}
        function cell(n) return {type='button',id=n,colspan=1,properties={active=true},handlers={onClick=function()end}} end
        selectcell=cell(30);expandcell=cell(31);confirmcell=cell(32)
        confirmcell.properties.active=function()return true end
        confirmcell.properties.helpOverlayID='trader_purchase_confirm'
        row={index=1,rowdata={'entry','lic'},[5]=selectcell}
        cat={index=1,rowdata={'category','ship'},[1]=expandcell}
        confirmrow={index=1,rowdata='buttonrow',[1]=confirmcell}
        top={type='table',id=20,rows={row}};bottom={type='table',id=21,rows={confirmrow}}
        frame={id=10,properties={layer=1},content={bottom,top}}
        m={name='BlueprintOrLicenceTraderMenu',shown=true,minimized=false,mode='licence',trader=42ULL,
            traderfaction='argon',tradecontainer=44ULL,playermoney=1000,uirelation=20,totalprice=0,
            licences={cached},licencestopurchase={},blueprintstopurchase={},blueprintlist={},
            table_wares={ship={ware=100}},expanded={},table_top=top,frame=frame,frames={[1]=10},buttonScriptMap={}}
        function rebuild_wrappers()
            m.buttonScriptMap={}
            for _,t in ipairs(frame.content)do for ri,rr in ipairs(t.rows)do for ci=1,5 do
                local c=rr[ci]
                if c and c.type=='button' then
                    local key=rr.rowdata
                    local function wrapper()
                        writes=writes+1
                        if callback then callback() end
                        if key=='buttonrow' then m.shown=false
                        elseif key[1]=='category' then m.expanded[key[2]]=not m.expanded[key[2]]
                        elseif m.mode=='licence' then m.licencestopurchase.lic={type='lic_type',price=100};m.totalprice=100
                        else m.blueprintstopurchase.ware=100;m.totalprice=100 end
                    end
                    m.buttonScriptMap[#m.buttonScriptMap+1]={layer=1,tableobj=t.id,row=ri,col=ci,type='onClick',button=c.id,script=wrapper}
                end
            end end end
        end
        menus={m};issued=42ULL
        d={ffi=ffi,C=C,globals=g,menus=function()native();return menus end,current=function()return stamp end,
            now=function()return age end,resolve_issued=function(t,k)native();assert(t=='trader' and k=='object');return issued end,
            component_id=function(v)return v end,valid_widget=function(v)if advance_during_read then age=16 end;return invalidwidget~=v end,
            cell_id=function(t,ri,ci)local tt=t==20 and top or bottom;return tt.rows[ri][ci].id end,
            widget_state=function(w)if nativeactive~=nil then return {active=nativeactive}end;return {active=true}end}
        function prepare()p=api.prepare_select('trader',m.mode,row.rowdata[2],100);return p end
        function confirm()
            if m.mode=='licence' then m.licencestopurchase.lic={type='lic_type',price=100}
            else m.blueprintstopurchase.ware=100 end
            m.totalprice=100;p=api.prepare_confirm('trader',m.mode,row.rowdata[2],100);return p
        end
    ''')
    if mode == 'blueprint':
        r.execute("m.mode='blueprint';row.rowdata[2]='ware';row.index=2;top.rows={cat,row}")
    r.execute('rebuild_wrappers()')
    r.globals().factory = r.execute(SOURCE.read_text())
    r.execute('api=factory(d)')
    return r


@pytest.mark.parametrize('mode', ['licence', 'blueprint'])
def test_constructor_is_lazy_and_observation_is_built_scope(mode):
    r=make(mode);assert r.eval('queries')==0
    out=r.eval("api.observe('trader',m.mode,1)")
    assert out.status=='OBSERVED' and out.full_catalogue is False and out.purchase_verified is False
    assert out.built_row_count==(1 if mode=='licence' else 2)
    assert out.rows[out.built_row_count].price==100
    assert out.observer_clock=='UI_ELAPSED' and r.eval('writes')==0


@pytest.mark.parametrize('mode', ['licence', 'blueprint'])
@pytest.mark.parametrize('phase', ['prepare', 'confirm'])
def test_real_wrapper_dispatch_once_no_purchase_claim(mode,phase):
    r=make(mode);assert r.eval(phase+'().status')=='PREPARED'
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.action_attempted is True and out.purchase_verified is False
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==1


def test_category_wrapper_requires_explicit_changed_state():
    r=make('blueprint')
    assert r.eval("api.prepare_expand('trader','blueprint','ship',false).status")=='REFUSED'
    r.execute("p=api.prepare_expand('trader','blueprint','ship',true)")
    assert r.eval('p.status')=='PREPARED'
    assert r.eval('api.execute(p.ticket).status')=='DISPATCHED_UNVERIFIED'
    assert r.eval('m.expanded.ship') is True and r.eval('writes')==1


@pytest.mark.parametrize('change',[
    "m.shown=false","m.minimized=true","m.mode='other'","m.queueupdate=true",
    "issued=43ULL","m.traderfaction='terran'","m.tradecontainer=45ULL","faction='terran'",
    "funds=999","funds=0/0","relation=21","licence.price=101","licence.type='wrong'",
    "licence.issellable=false","precursor=false","owned=true","licence.minrelation=30;cached.minrelation=30",
    "m.totalprice=1","m.blueprintstopurchase.ware=100","m.licencestopurchase.foreign={type='x',price=100};m.totalprice=100",
    "frame.id=11","m.frames[1]=11","invalidwidget=20","invalidwidget=30",
    "row.index=2","row.rowdata[2]='wrong'","row.rowdata[3]='extra'","selectcell.type='text'",
    "selectcell.properties.active=false","nativeactive=false","m.buttonScriptMap={}",
    "m.buttonScriptMap[3]=m.buttonScriptMap[2]","top.rows[2]=row","menus[2]=m",
    "menus[2]={name='Other',shown=true}","age=0/0","stamp=nil",
])
def test_refusal_before_any_dispatch(change):
    r=make();r.execute(change)
    assert r.eval('prepare().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('change',[
    "age=16","age=9","stamp.generation=string.rep('d',32)","issued=43ULL",
    "funds=999;m.playermoney=999","m.totalprice=100;m.licencestopurchase.lic={type='lic_type',price=100}",
    "licence.price=101;cached.price=101","owned=true","precursor=false",
    "frame.id=11;m.frames[1]=11","top.id=22","selectcell.handlers.onClick=function()end",
    "m.buttonScriptMap[2].script=function()writes=writes+1 end","api.dispose()",
])
def test_execute_revalidates_and_consumes_refused_ticket(change):
    r=make();r.execute('prepare();'+change)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r.execute('age=10;api.execute(p.ticket)');assert r.eval('writes')==0


@pytest.mark.parametrize('callback',["error('native failure')","stamp=nil","age=9"])
def test_post_dispatch_is_uncertain_not_nothing_acted(callback):
    r=make();r.execute('prepare();callback=function()'+callback+' end')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True and r.eval('writes')==1


@pytest.mark.parametrize('change',[
    "bpcount=1", "bpcount=8193", "bpcount=0/0", "m.table_wares.ship.ware=101",
    "m.table_wares.other={ware=100}", "m.blueprintlist.ware=true", "precursor=false",
    "missingware=true", "C.GetNumBlueprints=function()return nil end",
])
def test_blueprint_native_ownership_quote_refusals(change):
    r=make('blueprint');r.execute(change)
    assert r.eval('prepare().status')=='REFUSED' and r.eval('writes')==0


def test_ownership_change_between_native_reads_refuses():
    r=make('blueprint')
    r.execute("bpcount=1;C.GetBlueprints=function(a,n)a[0].ware=bpware or 'ware';bpware='other';return n end")
    assert r.eval("api.observe('trader','blueprint',1).status")=='REFUSED'


def test_public_before_mutation_cannot_retarget_private_ticket():
    r=make();r.execute("prepare();p.before.item.price=101;licence.price=101;cached.price=101")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def test_confirm_rejects_foreign_same_price_basket():
    r=make();r.execute("m.licencestopurchase.foreign={type='lic_type',price=100};m.totalprice=100")
    assert r.eval("api.prepare_confirm('trader','licence','lic',100).status")=='REFUSED'


def test_confirm_rejects_wrong_licence_type_in_basket():
    r=make();r.execute("m.licencestopurchase.lic={type='wrong',price=100};m.totalprice=100")
    assert r.eval("api.prepare_confirm('trader','licence','lic',100).status")=='REFUSED'


def test_blueprint_owned_row_read_is_not_missing_button_failure():
    r=make('blueprint');r.execute("bpcount=1;m.blueprintlist.ware=true;row[5]={type='text'}")
    out=r.eval("api.observe('trader','blueprint',1)")
    assert out.status=='OBSERVED' and out.rows[2].owned is True and out.rows[2].active is False


@pytest.mark.parametrize('start',[0,4,1.5,float('inf')])
def test_row_window_out_of_scope_refuses(start):
    r=make();r.globals().start=start
    assert r.eval("api.observe('trader','licence',start).status")=='REFUSED'


def test_captured_native_dependency_is_not_replaced_after_constructor():
    r=make('blueprint');r.execute("g.GetWareData=function()error('replacement must not run')end")
    assert r.eval('prepare().status')=='PREPARED'


def test_revalidation_time_can_expire_ticket_before_native_dispatch():
    r=make('blueprint');r.execute('prepare();advance_during_read=true')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='REFUSED' and out.action_attempted is False
    assert 'expired before dispatch' in out.reason and r.eval('writes')==0

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
            now=function()return age end,resolve_issued=function(t,k)native();if resolve_error then error(resolve_error)end;assert(t=='trader' and k=='object');return issued end,
            component_id=function(v)return v end,valid_widget=function(v)if advance_during_read then age=16 end;return invalidwidget~=v end,
            cell_id=function(t,ri,ci)local tt=t==20 and top or bottom;return tt.rows[ri][ci].id end,
            widget_state=function(w)if nativeactive~=nil then return {active=nativeactive}end;return {active=true}end,
            representative=function(f)native();assert(f=='argon');return rep or 42ULL end,
            convert_parameters=function(p)conversions=(conversions or 0)+1;if convertbad then return nil end;return p end,
            open_menu=function(n,p)opens=(opens or 0)+1;opened=n;openparams=p
                if openerror then error(openerror)end;if openshows then m.shown=true end end}
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
    r.globals().factory = r.execute(SOURCE.read_text(encoding="utf-8"))
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


# --- licence trader opener -------------------------------------------------------------
# Vanilla opens the menu from the representative conversation with
# choiceparam [0, 0, $FactionRepresentative, 'licence'] (md/npc_factionrepresentative.xml:272,
# opened at :295); the menu reads param[3] as the trader and param[4] as the mode
# (menu_trader_blueprintsorlicences.lua:126-139).

def closed(mode='licence'):
    r=make(mode);r.execute('m.shown=false;opens=0;conversions=0');return r


def test_open_happy_path_dispatches_once_and_read_back_confirms():
    r=closed();r.execute("openshows=true;o=api.prepare_open('trader','licence')")
    assert r.eval('o.status')=='PREPARED' and r.eval('o.action_attempted') is False
    assert r.eval('o.trader')=='42' and r.eval('o.faction')=='argon' and r.eval('o.mode')=='licence'
    assert r.eval('opens')==0
    out=r.eval('api.execute(o.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.action_attempted is True
    assert out.menu_open_verified is False and out.purchase_verified is False and out.kind=='open'
    assert r.eval('opened')=='BlueprintOrLicenceTraderMenu' and r.eval('conversions')==1
    assert r.eval('#openparams')==4 and r.eval('openparams[1]')==0 and r.eval('openparams[2]')==0
    assert r.eval('openparams[3]==42ULL') is True and r.eval('openparams[4]')=='licence'
    r.execute('api.execute(o.ticket)');assert r.eval('opens')==1
    seen=r.eval("api.observe('trader','licence',1)")
    assert seen.status=='OBSERVED' and seen.trader=='42' and seen.mode=='licence'
    # Opening never buys: no button wrapper ran and the basket is still empty.
    assert r.eval('writes')==0 and r.eval('next(m.licencestopurchase)') is None


@pytest.mark.parametrize('after',['','m.shown=false',"m.shown='yes'",'m.minimized=nil',"m.mode='blueprint'"])
def test_open_attempt_with_closed_or_unknown_menu_state_is_not_reported_open(after):
    r=closed();r.execute("openshows=true;o=api.prepare_open('trader','licence')")
    if not after:r.execute('openshows=false')
    out=r.eval('api.execute(o.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.menu_open_verified is False
    r.execute(after)
    seen=r.eval("api.observe('trader','licence',1)")
    assert seen.status=='REFUSED' and seen.action_attempted is False


@pytest.mark.parametrize('change',[
    'm.shown=true',"menus[2]={name='Other',shown=true}",'menus={}',
    "menus[2]={name='BlueprintOrLicenceTraderMenu'}","menus[2]='row'",
    "resolve_error='token not issued in this session'","resolve_error='stale token: issued before reload'",
    'issued=nil','rep=43ULL','rep=0ULL',"faction=''",'stamp=nil','age=0/0','api.dispose()',
])
def test_open_refuses_before_any_native_open(change):
    r=closed();r.execute(change)
    out=r.eval("api.prepare_open('trader','licence')")
    assert out.status=='REFUSED' and out.action_attempted is False and len(out.reason)>0
    assert r.eval('opens')==0 and r.eval('writes')==0


def test_open_unregistered_trader_menu_names_the_refusal():
    r=closed();r.execute('menus={}')
    out=r.eval("api.prepare_open('trader','licence')")
    assert out.status=='REFUSED' and 'trader menu not registered' in out.reason and r.eval('opens')==0


@pytest.mark.parametrize('mode',['blueprint','purchase',None])
def test_open_mode_is_closed_to_licence(mode):
    r=closed();r.globals().mode=mode
    out=r.eval("api.prepare_open('trader',mode)")
    assert out.status=='REFUSED' and r.eval('opens')==0


@pytest.mark.parametrize('token',['other',None,'',{}])
def test_open_refuses_unissued_or_malformed_token_without_lua_error(token):
    r=closed();r.globals().tok=token
    r.execute("resolve_error=nil;ok,out=pcall(api.prepare_open,tok,'licence')")
    assert r.eval('ok') is True and r.eval('out.status')=='REFUSED' and r.eval('opens')==0


@pytest.mark.parametrize('change',[
    'm.shown=true',"menus[2]={name='Other',shown=true}",'age=16','age=9',
    "stamp.generation=string.rep('d',32)",'rep=43ULL','issued=43ULL',
    "resolve_error='stale token'",'convertbad=true','api.dispose()',
    "menus={{name='BlueprintOrLicenceTraderMenu'}}",
])
def test_open_execute_revalidates_and_consumes_refused_ticket(change):
    r=closed();r.execute("o=api.prepare_open('trader','licence');"+change)
    out=r.eval('api.execute(o.ticket)')
    assert out.status=='REFUSED' and out.action_attempted is False and r.eval('opens')==0
    r.execute("age=10;rep=nil;resolve_error=nil;api.execute(o.ticket)");assert r.eval('opens')==0


def test_open_native_failure_after_dispatch_is_uncertain():
    r=closed();r.execute("openerror='native open failed';o=api.prepare_open('trader','licence')")
    out=r.eval('api.execute(o.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True and r.eval('opens')==1


def test_open_ticket_cannot_drive_a_purchase_button():
    r=closed();r.execute("o=api.prepare_open('trader','licence');openshows=true;api.execute(o.ticket)")
    # The open ticket is consumed; buying still needs its own prepare_select/confirm tickets.
    again=r.eval('api.execute(o.ticket)')
    assert again.action_attempted is True and r.eval('opens')==1 and r.eval('writes')==0

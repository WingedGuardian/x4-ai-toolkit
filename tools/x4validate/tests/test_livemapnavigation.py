"""Actual Lua fixed map navigation with synthetic native models; no game calls."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/'mods/x4_toolkit_helper/ui/live_mapnavigation.lua'


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        writes=0;reads=0;time=10;issued=42ULL;active=true
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        function callback()writes=writes+1;if after_write then after_write()end end
        cell={type='button',id=30,colspan=1,properties={active=true,helpOverlayID='mapst_ao_information'},handlers={onClick=callback}}
        row={cell,index=1,rowdata='orders_tabs'};tbl={type='table',id=20,rows={row}}
        frame={id=10,properties={layer=4},content={tbl}}
        m={name='MapMenu',shown=true,minimized=false,infoTableMode='info',infoSubmenuObject=42ULL,
            infoMode={left='objectcrew'},panelState={leftmenu=true,leftbar=false},infoFrame=frame,
            orderHeaderTable=tbl,frames={[4]=10},buttonScriptMap={
            {layer=4,tableobj=20,row=1,col=1,type='onClick',button=30,script=callback}}}
        menus={m}
        d={menus=function()reads=reads+1;return menus end,public_current=function()return stamp end,clock=function()return time end,
            resolve_issued=function(t,k)assert(t=='context' and k=='object');return issued end,component_id=function(v)return v end,
            valid_widget=function(v)return v==10 or v==20 or v==30 end,cell_id=function(t,i,c)assert(t==20 and i==1 and c==1);return cell.id end,
            widget_state=function(v)assert(v==30);return {active=active}end,
            convert_parameters=function(p)assert(p[1]==0 and p[2]==0 and p[3]==true and p[4]==nil and p[5]==nil and p[6]=='infomode' and p[7][1]=='info' and p[7][2]==42ULL);return p end,
            open_menu=function(name,p)assert(name=='MapMenu' and p[6]=='infomode');callback()end}
        function prep()p=api.prepare('context','left','objectinfo');return p end
    """)
    r.globals().factory=r.execute(SOURCE.read_text());r.execute('api=factory(d)');return r


def check_tab_handler_once_no_ui_effect_claim():
    r=runtime();assert r.eval('reads+writes')==0
    assert r.eval("api.observe('context','left').submode")=='objectcrew'
    assert r.eval('prep().status')=='PREPARED'
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='DISPATCHED_UNVERIFIED' and out.info_open_verified is False
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==1


def check_closed_map_fixed_parameter_route():
    r=runtime();r.execute("m.shown=false;p=api.prepare('context','left','open')")
    assert r.eval('p.status')=='PREPARED'
    assert r.eval('api.execute(p.ticket).status')=='DISPATCHED_UNVERIFIED' and r.eval('writes')==1


@pytest.mark.parametrize('side',['left','right'])
def check_panel_button_actual_wrapper(side):
    r=runtime();r.globals().side=side
    r.execute("""
        m.panelMode=true;m.panelState[side..'menu']=false;m.panelState[side..'bar']=true
        m.mainFrame=frame;frame.properties.layer=6;m.frames[6]=10
        m[side=='left' and 'sideBar' or 'rightBar']=20
        m[side=='left' and 'infoTableMode' or 'searchTableMode']='info';m.infoMode[side]='objectcrew'
        row.rowdata='info';cell.properties.helpOverlayID=side=='left' and 'map_sidebar_information' or 'map_sidebar_information2'
        m.buttonScriptMap[1].layer=6;p=api.prepare('context',side,'panel')
    """)
    assert r.eval('p.status')=='PREPARED' and r.eval('api.execute(p.ticket).status')=='DISPATCHED_UNVERIFIED'
    assert r.eval('writes')==1


@pytest.mark.parametrize('change',[
    'm.shown=false','m.minimized=true',"m.mode='trade'","m.contextMenuMode='trade'",'m.createMainFrameRunning=true',
    'm.createInfoFrameRunning=true','m.showMultiverse=true','m.infoTableMode=nil;m.searchTableMode="info"',
    'm.infoSubmenuObject=43ULL','issued=43ULL','m.panelMode=true;m.panelState.leftmenu=false',
    'frame.id=11','frame.properties.layer=5','m.frames[4]=11','m.orderHeaderTable={id=20}',
    "row.rowdata='other'",'row.index=2',"cell.properties.helpOverlayID='other'",'active=false',
    'm.buttonScriptMap={}','m.buttonScriptMap[2]=m.buttonScriptMap[1]',
    'cell.id=31','cell.handlers.onClick=nil','stamp=nil','time=0/0',"menus[2]={name='Other',shown=true}",
])
def check_invalid_model_never_dispatches(change):
    r=runtime();r.execute(change)
    assert r.eval('prep().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('change',[
    'time=41','time=9',"stamp.generation='other'",'issued=43ULL','m.infoMode.left="standingorders"',
    'frame.id=11;m.frames[4]=11','tbl.id=21','cell.handlers.onClick=function()end',
    'm.buttonScriptMap[1].script=function()end','api.dispose()',
])
def check_stale_preparation_consumes_without_write(change):
    r=runtime();r.execute('prep();'+change)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r.execute('time=10;api.execute(p.ticket)');assert r.eval('writes')==0


@pytest.mark.parametrize('change',["error('callback failure')",'stamp=nil','time=9'])
def check_after_write_stays_uncertain(change):
    r=runtime();r.execute('prep();after_write=function()'+change+' end')
    out=r.eval('api.execute(p.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True and r.eval('writes')==1


def check_no_arbitrary_open_or_step():
    r=runtime();r.execute('m.shown=false')
    assert r.eval("api.prepare('context','right','open').status")=='REFUSED'
    assert r.eval("api.prepare('context','left','save').status")=='REFUSED'
    assert r.eval('writes')==0


def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith('check_') or not callable(fn):continue
        marks=[m for m in getattr(fn,'pytestmark',[]) if m.name=='parametrize']
        if marks:
            assert len(marks)==1
            for i,v in enumerate(marks[0].args[1]):out.append(pytest.param(name,[v],id=f'{name}-{i}'))
        else:out.append(pytest.param(name,[],id=name))
    return out


@pytest.mark.parametrize('name,args',_cases())
def test_map_navigation_control(name,args):
    p=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=15,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip()=='CONTROL_PASSED',p.stdout+p.stderr


if __name__=='__main__':
    name=sys.argv[1];assert name.startswith('check_') and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print('CONTROL_PASSED')

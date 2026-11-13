"""Actual Lua options adapter with synthetic native widgets; no game calls."""
import json,os,subprocess,sys
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

SOURCE=Path(__file__).resolve().parents[3]/'mods/x4_toolkit_helper/ui/live_options.lua'


def build(before=''):
    r=LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        time=10;reads=0;writes=0;confirmed=0;opened=0;native_active=true
        stamp={provider='x4-live-session-v1',revision=string.rep('a',64),session=string.rep('b',32),
            generation=string.rep('c',32),world_token='world'}
        raw={id='example_options',submenu='example_options',name='Example'}
        row1={rowdata=raw,index=1,properties={interactive=true}}
        cell={type='dropdown',id=30,colspan=1,properties={active=true,options={
            {id=1,text='High',value=5},{id=2,text='Low',value=0}}},handlers={onDropDownConfirmed=function()end}}
        row2={{type='text'},{type='text'},cell,rowdata=true,index=2,properties={interactive=true}}
        tbl={type='table',id=20,rows={row1,row2}};frame={id=10,properties={layer=4},content={tbl}}
        native_options={{id='1',text='High',active=true},{id='2',text='Low',active=true}}
        function wrapper(widget,option) assert(widget==30);writes=writes+1;confirmed=confirmed+1;last_option=option;if after_write then after_write()end end
        function select_row(tableid,modified,row,isdblclick,input)
            assert(tableid==20 and modified==false and row==1 and isdblclick==false and input=='mouse')
            writes=writes+1;opened=opened+1;if after_write then after_write()end
        end
        m={name='OptionsMenu',isStartmenu=false,shown=true,minimized=false,currentOption='simple_menu_extension_options',
            optionsFrame=frame,frames={[4]=10},optionTable=20,rowDataMap={[20]={[1]=raw,[2]=true}},
            onSelectElement=select_row,openSubmenu=function()end,submenuHandler=function()end,
            dropdownScriptMap={{layer=4,tableobj=20,row=2,col=3,type='onDropDownConfirmed',script=wrapper}}}
        menus={m}
        d={menus=function()reads=reads+1;return menus end,current=function()return stamp end,now=function()return time end,
            valid_widget=function(id)return id==10 or id==20 or id==30 end,
            cell_id=function(t,ri,ci) assert(t==20 and ri==2 and ci==3);return cell.id end,
            widget_state=function(id,kind)assert(id==30 and kind=='dropdown');return{active=native_active,options=native_options}end,
            set_dropdown_current=function(id,option)assert(id==30);writes=writes+1;setter_option=option;if setter_error then error('setter failure')end end,
            open_menu=function(name,page)assert(name=='OptionsMenu' and page=='main');writes=writes+1;opened=opened+1;if after_write then after_write()end end}
        function prep() p=api.prepare_select(m.currentOption,2,3,30,'1','High','number','5');return p end
        function subprep() p=api.prepare_submenu(m.currentOption,'example_options','example_options');return p end
    """+before)
    r.globals().factory=r.execute(SOURCE.read_text(encoding="utf-8"));r.execute('api=factory(d)');return r


def check_pure_constructor_observation_and_index_value_mapping():
    r=build();assert r.eval('reads+writes')==0
    out=r.eval('api.observe(m.currentOption)')
    assert out.status=='OBSERVED' and out.authored_widget_id=='NOT_EXPOSED'
    option=out.dropdowns[1].options[1]
    assert option.id=='1' and option.authored_value=='5' and option.authored_value_type=='number'
    assert out.setting_effect_verified is False and r.eval('writes')==0
    assert r.eval('prep().status')=='PREPARED'
    result=r.eval('api.execute(p.ticket)')
    assert result.status=='DISPATCHED_UNVERIFIED' and result.action_attempted is True
    assert r.eval('last_option')=='1' and r.eval('setter_option')=='1'
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==2 and r.eval('confirmed')==1


def check_native_submenu_row_handler_once():
    r=build();assert r.eval('subprep().status')=='PREPARED'
    out=r.eval('api.execute(p.ticket)');assert out.status=='DISPATCHED_UNVERIFIED'
    r.execute('api.execute(p.ticket)');assert r.eval('opened')==1 and r.eval('writes')==1


def check_open_main_only_with_native_pause_disclosed():
    r=build('m.shown=false;');assert r.eval('api.prepare_open().status')=='PREPARED'
    out=r.eval("api.execute('1')")
    assert out.status=='DISPATCHED_UNVERIFIED' and out.native_pause_possible is True
    assert r.eval('opened')==1


@pytest.mark.parametrize('code',[
    'm.shown=false','m.isStartmenu=true','m.minimized=true',"m.contextMenuMode='question'",'m.userQuestion={}',
    'frame.properties.layer=2','m.frames[4]=11','m.optionTable=21','m.rowDataMap[20][2]=false',
    'cell.id=0','m.dropdownScriptMap={}','m.dropdownScriptMap[2]=m.dropdownScriptMap[1]',
    'native_active=false','native_options[1].active=false',"native_options[1].id='5'",
    "native_options[1].text='Other'",'cell.properties.options[1].value=nil',
    "cell.properties.options[1].value='5'",'cell.properties.options[1].value=0/0',
    "menus[2]={name='Other',shown=true}",'stamp=nil','time=0/0',
])
def check_wrong_view_model_or_unknown_value_refuses(code):
    r=build(code+';');assert r.eval('prep().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('page',['save','load','extensions','bonus','new','profile_load'])
def check_special_native_pages_cannot_dispatch_submenu(page):
    r=build();r.globals().page=page;r.execute('m.currentOption=page')
    assert r.eval('subprep().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('code',[
    "raw.callback=function()writes=writes+1 end",'row1.properties.interactive=false',
    "raw.submenu='other'",'m.rowDataMap[20][1]={}',
])
def check_nonpure_submenu_cannot_dispatch(code):
    r=build(code+';');assert r.eval('subprep().status')=='REFUSED' and r.eval('writes')==0


@pytest.mark.parametrize('code',[
    'time=41','time=9',"stamp.generation=string.rep('d',32)","m.currentOption='other'",
    'frame.id=11;m.frames[4]=11;d.valid_widget=function()return true end',
    'cell.properties.options[1].value=4','cell.handlers.onDropDownConfirmed=function()end',
    'm.dropdownScriptMap[1].script=function()end','native_active=false',
])
def check_prepared_model_drift_consumes_without_write(code):
    r=build();r.execute('prep();'+code)
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0
    r.execute('time=10;api.execute(p.ticket)');assert r.eval('writes')==0


@pytest.mark.parametrize('code',["error('callback failed')",'stamp=nil','time=9'])
def check_post_write_failure_preserves_attempt(code):
    r=build();r.execute('prep();after_write=function()'+code+' end')
    out=r.eval('api.execute(p.ticket)');assert out.status=='UNCERTAIN' and out.action_attempted is True
    assert r.eval('writes')==2


def check_setter_failure_does_not_dispatch_or_retry_callback():
    r=build();r.execute('prep();setter_error=true');out=r.eval('api.execute(p.ticket)')
    assert out.status=='UNCERTAIN' and out.action_attempted is True
    r.execute('api.execute(p.ticket)');assert r.eval('writes')==1 and r.eval('confirmed')==0


def check_public_model_cannot_rebind_private_expected_value():
    r=build();r.execute("prep();p.model.options[1].authored_value='4';cell.properties.options[1].value=4")
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def check_retirement_prevents_native_write():
    r=build();r.execute('prep();api.dispose()')
    assert r.eval('api.execute(p.ticket).status')=='REFUSED' and r.eval('writes')==0


def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith('check_') or not callable(fn):continue
        marks=[m for m in getattr(fn,'pytestmark',[]) if m.name=='parametrize']
        if marks:
            assert len(marks)==1
            for i,value in enumerate(marks[0].args[1]):out.append(pytest.param(name,[value],id=f'{name}-{i}'))
        else:out.append(pytest.param(name,[],id=name))
    return out


@pytest.mark.parametrize('name,args',_cases())
def test_native_options_control(name,args):
    p=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),name,json.dumps(args)],
        capture_output=True,text=True,timeout=15,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert p.returncode==0,p.stdout+p.stderr
    assert p.stdout.strip()=='CONTROL_PASSED',p.stdout+p.stderr


if __name__=='__main__':
    name=sys.argv[1];assert name.startswith('check_') and callable(globals()[name])
    globals()[name](*json.loads(sys.argv[2]));print('CONTROL_PASSED')

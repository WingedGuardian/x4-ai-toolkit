"""Generated extension + actual adapters/SDK/wire, synthetic native bindings only."""
import hashlib,importlib.util,json,os,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import pytest
from test_livetradermenu import make

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('tradertext_extension',ROOT/'scripts/live_tradertext_extension.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


def runtime(mode='licence',lua_subject=False,profile=None):
    r=make(mode);files,receipt=g.prepare()
    r.globals().sdk=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actions.lua').read_text(encoding="utf-8"))
    r.globals().wire_factory=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actionwire.lua').read_text(encoding="utf-8"))
    r.execute('''
        wire=wire_factory();registry=sdk();disposers={};converted=0;property_reads=0;context_reads=0
        X4ToolkitAutomationBundle={register_extension=function(v)descriptor=v end}
        function u(s)local n=0ULL;for i=1,#s do n=n*10+tonumber(s:sub(i,i))end;return n end
        original_data=g.GetComponentData;original_context=g.GetContextByClass
        g.ConvertIDTo64Bit=function(v)
            if type(v)=='cdata'then return v end
            if type(v)=='number'then return v end
            local digits=v:match('^LUA:(%d+)ULL$')
            assert(digits,'unknown LuaID')
            if wrongroundtrip then return 99ULL end
            if inexactroundtrip then return 9007199254740992 end
            if safeinteger then return tonumber(digits)end
            return u(digits)
        end
        g.ConvertStringToLuaID=function(v)
            converted=converted+1;assert(v:match('ULL$'))
            if missinglua then return nil end
            if zerolua then return 0 end
            return 'LUA:'..v
        end
        g.GetComponentData=function(v,k,k2,k3,k4)
            property_reads=property_reads+1;assert(type(v)=='string' and v:match('^LUA:'))
            if k=='name' then assert(k2=='typestring' and k3=='rolename' and k4=='owner');return 'Observed actor','npc','representative','synthetic' end
            if k=='macro' then assert(k2=='ismodule');return 'ship_macro',false end
            return original_data(u(v:match('^LUA:(%d+)ULL$')),k)
        end
        C.GetContextByClass=function(v,k,b)
            context_reads=context_reads+1;assert(type(v)=='cdata' and ffi.istype('uint64_t',v))
            return original_context(v,k,b)
        end
        g.getElapsedTime=function()return age end
        g.IsValidWidgetElement=d.valid_widget
        g.GetCellContent=function(t,ri,ci)if t==120 then return etable.rows[ri][ci].id end;return d.cell_id(t,ri,ci)end
        g.IsButtonActive=function(id)return d.widget_state(id).active end
        g.GetMacroData=function(m,k)assert(m=='ship_macro' and k=='infolibrary');return 'shiptypes_l' end
        g.OpenMenu=function(n,p)opened=n;openparams=p;opens=(opens or 0)+1;if onopen then onopen()end end
        C.IsComponentClass=function(v,k)return k=='ship' end
        C.IsStoryFeatureUnlocked=function()return true end
        C.GetFactionRepresentative=function(f)assert(f=='argon');return representative or 42ULL end
        etable={type='table',id=120,numcolumns=1,rows={{type='row',index=1}}}
        eframe={id=110,properties={layer=1},content={etable}}
        emenu={name='EncyclopediaMenu',shown=false,minimized=false,encyclopediaMode='encyclopedia',
            frame=eframe,frames={[1]=110},table_description=120,mode='Ships',library='ships_xl',id='arawn_macro'}
        -- The trader fixture has its own cell factory; encyclopedia cells need actual text properties.
        etable.rows[1][1]={type='text',id=130,colspan=1,properties={text='Arawn'}}
        menus[2]=emenu;g.Menus=menus
        function show_text()m.shown=false;emenu.shown=true end
    ''')
    if lua_subject:r.execute("m.trader='LUA:42ULL';m.tradecontainer='LUA:44ULL'")
    if profile is not None:
        spec=importlib.util.spec_from_file_location('text_capacity',ROOT/'tools/x4validate/x4validate/_livecapacity.py')
        capacity=importlib.util.module_from_spec(spec);spec.loader.exec_module(capacity)
        r.globals().capacity_resolver=r.execute(capacity.lua_source())
        r.globals().capacity_profile=profile
        r.globals().capacity_revision=capacity.resolve(profile)['revision']
    r.execute(files['ui/live_tradertext_extension.lua'].decode())
    r.execute('''
        attached=descriptor.attach{globals=g,ffi=ffi,C=C,public_current=d.current,encode=wire.encode,
            Helper={convertComponentIDs=function(p)return p end},
            capacity_profile=capacity_profile,capacity_revision=capacity_revision,capacity_resolver=capacity_resolver,
            resolve_issued=d.resolve_issued,on_dispose=function(fn)disposers[#disposers+1]=fn end,registry=registry}
        seq=0
        function request(action,args)
            seq=seq+1;req={version=1,id=string.format('%032x',seq),digest=string.format('%064x',seq),
                session=stamp.session,provider='x4-trader-text-v1',binding=descriptor.revision,action=action,arguments=args or {}}
        end
        function prep()return registry.prepare(req)end
        function run()return registry.execute(req.id,req.session,req.digest)end
        function status()return registry.status(req.id,req.session,req.digest)end
        function observe_trader()
            request('trader.observe',{trader='trader',mode=m.mode,start=1});prep();result=run();capture_id=req.id
        end
        function prepare_select()
            request('trader.select',{trader='trader',mode=m.mode,item=row.rowdata[2],price=100});return prep()
        end
    ''')
    return r,receipt


def body(r):return json.loads(r.eval('result.observations.payload_json'))


def retrieve(r,header):
    blocks=[]
    for i in range(header['pages']):
        r.globals().page=i;r.globals().cid=header['capture']
        r.execute("request('page',{capture=cid,index=page});prep();result=run()")
        assert r.eval('result.status')=='completed'
        p=body(r);assert p['offset']==i*500 and p['index']==i and p['bytes']==header['bytes']
        b=bytes.fromhex(p['hex']);assert len(b)==p['length'];blocks.append(b)
        assert len(r.eval('wire.encode({payload=wire.encode(result)})').encode())<=4096
    joined=b''.join(blocks);assert len(joined)==header['bytes'];return json.loads(joined)


def check_factory_exact_scope_and_pins():
    files,receipt=g.prepare();assert set(files)=={'ui/live_tradertext_extension.lua'}
    assert not receipt['runtime_verified'] and receipt['menu_opening_supported']
    for p,h in receipt['source_sha256'].items():assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
    assert hashlib.sha256(files['ui/live_tradertext_extension.lua']).hexdigest()==receipt['output_sha256']['ui/live_tradertext_extension.lua']


@pytest.mark.parametrize('target',['trader','text','navigation','actor','provider','self'])
def check_source_drift_refuses(target):
    read=Path.read_bytes;n=0;selected=Path(g.__file__) if target=='self' else ROOT/g.PATHS[target]
    def changed(p):
        nonlocal n
        b=read(p)
        if p==selected:
            n+=1
            if n>1:return b+b'\n'
        return b
    with patch.object(Path,'read_bytes',changed),pytest.raises(ValueError):g.prepare()


def check_attach_is_lazy_and_owns_all_cleanup():
    r,_=runtime();assert r.eval('queries+writes+converted+property_reads+context_reads')==0
    assert r.eval('#disposers')==5
    r.execute("request('trader.observe',{trader='trader',mode='licence',start=1});prep()")
    assert r.eval('queries+writes+converted')==0


@pytest.mark.parametrize('lua_subject',[False,True])
@pytest.mark.parametrize('mode',['licence','blueprint'])
def check_actual_menu_subject_forms_normalize_and_observe(mode,lua_subject):
    r,_=runtime(mode,lua_subject);r.execute('observe_trader()')
    assert r.eval('result.status')=='completed';h=body(r)
    out=retrieve(r,h)
    assert out['status']=='OBSERVED' and out['trader']=='42' and out['container']=='44'
    assert out['mode']==mode and not out['purchase_verified']
    assert r.eval('converted')==r.eval('property_reads')>0 and r.eval('writes')==0


@pytest.mark.parametrize('change',['missinglua=true','zerolua=true','wrongroundtrip=true','inexactroundtrip=true'])
def check_invalid_lua_identity_fails_before_raw_property_read(change):
    r,_=runtime();r.execute(change+';observe_trader()')
    assert r.eval('result.status')=='unknown' and r.eval('property_reads+writes')==0
    assert retrieve(r,body(r))['status']=='REFUSED'


def check_safe_integer_native_roundtrip_is_supported():
    r,_=runtime();r.execute('safeinteger=true;observe_trader()')
    assert r.eval('result.status')=='completed'


@pytest.mark.parametrize('mode',['licence','blueprint'])
@pytest.mark.parametrize('action',['select','confirm','expand'])
def check_real_trader_action_through_sdk_is_once_only_not_purchase(mode,action):
    r,_=runtime(mode)
    if action=='expand' and mode=='licence':
        r.execute("request('trader.expand',{trader='trader',mode='licence',item='ship',expanded=true})")
        assert r.eval('prep().status')=='refused' and r.eval('writes')==0;return
    if action=='confirm':
        r.execute("if m.mode=='licence' then m.licencestopurchase.lic={type='lic_type',price=100}else m.blueprintstopurchase.ware=100 end;m.totalprice=100")
    if action=='expand':r.execute("request('trader.expand',{trader='trader',mode=m.mode,item='ship',expanded=true})")
    else:r.globals().act=action;r.execute("request('trader.'..act,{trader='trader',mode=m.mode,item=row.rowdata[2],price=100})")
    assert r.eval('prep().status')=='prepared' and r.eval('writes')==0
    out=r.eval('run()');assert out.status=='running' and out.observations.purchase_verified is False
    assert r.eval('writes')==1
    r.execute('run();status();status()');assert r.eval('writes')==1 and r.eval('status().status')=='running'


def check_licence_trader_open_through_sdk_is_once_only_and_read_back_confirms():
    r,_=runtime();r.execute("""
        m.shown=false;opens=0
        onopen=function()m.shown=true end
        request('trader.open',{trader='trader',mode='licence'});prepared=prep();result=run()
    """)
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    assert body(r)['native_status']=='DISPATCHED_UNVERIFIED' and body(r)['action']=='trader.open'
    assert body(r)['menu_open_verified'] is False and r.eval('result.observations.purchase_verified') is False
    assert r.eval('opened')=='BlueprintOrLicenceTraderMenu' and r.eval('#openparams')==4
    assert r.eval('openparams[3]==42ULL') is True and r.eval("openparams[4]=='licence'") is True
    r.execute('run();status()');assert r.eval('opens')==1 and r.eval('writes')==0
    r.execute('observe_trader()');assert r.eval('result.status')=='completed'
    seen=retrieve(r,body(r));assert seen['status']=='OBSERVED' and seen['trader']=='42'


@pytest.mark.parametrize('change',[
    "req.arguments.mode='blueprint'","req.arguments.item='lic'","req.arguments.trader={}",
    'representative=43ULL','representative=0ULL','m.shown=true',
])
def check_licence_trader_open_refusals_never_open(change):
    r,_=runtime();r.execute("m.shown=false;opens=0;request('trader.open',{trader='trader',mode='licence'})")
    assert r.eval('prep().status')=='prepared'  # twin: the unchanged request is accepted
    r.execute("request('trader.open',{trader='trader',mode='licence'});"+change)
    assert r.eval('prep().status')=='refused' and r.eval('opens')==0


def check_private_ticket_lifetime_refuses_before_dispatch():
    r,_=runtime();r.execute('prepare_select();age=16')
    assert r.eval('run().status')=='refused' and r.eval('writes')==0


def check_unknown_dispatch_is_not_replayed():
    r,_=runtime();r.execute("prepare_select();callback=function()error('native failed')end")
    assert r.eval('run().status')=='unknown' and r.eval('writes')==1
    r.execute('run();status()');assert r.eval('writes')==1


def check_text_unknown_callback_and_unicode_bytes_survive_actual_wire_pages():
    r,_=runtime();r.execute("show_text();etable.rows[1][1].properties.text=string.rep('é',600);etable.rows[2]={{type='text',id=131,colspan=1,properties={text=function()error('must not run')end}},index=2};request('text.observe',{section='description',start=1});prep();result=run()")
    assert r.eval('result.status')=='completed';out=retrieve(r,body(r))
    assert out['cells'][0]['text']=='é'*600 and out['cells'][1]['status']=='UNKNOWN'
    assert not out['all_text_descriptors_known'] and not out['rendered_pixels_verified']


def check_pages_are_immutable_not_menu_rereads():
    r,_=runtime();r.execute('observe_trader()');h=body(r);reads=r.eval('queries')
    r.execute('m.shown=false');out=retrieve(r,h)
    assert out['status']=='OBSERVED' and r.eval('queries')==reads


@pytest.mark.parametrize('change',["age=311","age=9","stamp.generation=string.rep('d',32)","attached.dispose()"])
def check_page_expiry_and_lifetime_refuse(change):
    r,_=runtime();r.execute("observe_trader();request('page',{capture=capture_id,index=0});prep();"+change)
    assert r.eval('run().status') not in ('completed','running')


def check_superseded_or_foreign_capture_refuses():
    r,_=runtime();r.execute("observe_trader();old=capture_id;observe_trader();request('page',{capture=old,index=0})")
    assert r.eval('prep().status')!='prepared'
    r.execute("request('page',{capture=string.rep('f',32),index=0})")
    assert r.eval('prep().status')!='prepared'


@pytest.mark.parametrize('change',["req.binding=string.rep('f',64)","req.arguments.extra=true","req.arguments.mode='purchase'","req.arguments.start=0","req.arguments.trader={}"])
def check_closed_arguments_refuse_before_any_native_read(change):
    r,_=runtime();r.execute("request('trader.observe',{trader='trader',mode='licence',start=1});"+change)
    assert r.eval('prep().status') not in ('prepared','completed') and r.eval('queries')==0


def check_changed_request_refuses_and_original_prepared_copy_is_immutable():
    r,_=runtime();r.execute('prepare_select();req.arguments.price=101')
    r.execute('accepted,why=pcall(prep)')
    assert r.eval('accepted') is False and 'request id reused with changed content' in r.eval('why')
    assert r.eval('writes')==0
    # Execute takes only original id/session/digest. Mutating the caller object cannot
    # rewrite the SDK's retained 100-credit request or its actual native ticket.
    assert r.eval('run().status')=='running' and r.eval('writes')==1
    assert r.eval('m.licencestopurchase.lic.price')==100


def check_fixed_root_open_through_actual_sdk():
    r,_=runtime();r.execute("m.shown=false;request('text.open',{});prepared=prep();result=run()")
    assert r.eval('prepared.status')=='prepared'
    assert r.eval('result.status')=='running' and r.eval('opened')=='EncyclopediaMenu'
    assert r.eval('openparams[1]')==0 and r.eval('openparams[2]')==0
    r.execute('run();status()');assert r.eval('opens')==1


def check_native_component_entry_authority():
    r,_=runtime();r.execute("m.shown=false;request('text.open_component',{subject='trader'});prepared=prep();result=run()")
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    assert r.eval('openparams[5]')=='ship_macro'
    r.execute("request('text.open_component',{subject='spoof'});prepared=prep()")
    assert r.eval('prepared.status')!='prepared'


def check_native_entry_rows_paged_and_selected():
    r,_=runtime();r.execute("""
        show_text();emenu.table_index=120;etable.numcolumns=6
        entry={'Wares','wares',{id='energycells'}}
        etable.rows[1].rowdata=false
        erow={index=2,rowdata=entry};etable.rows[2]=erow
        for i=1,6 do
            etable.rows[1][i]={type='text',id=140+i,colspan=1,properties={text='header'}}
            erow[i]={type='text',id=150+i,colspan=1,properties={text='Energy Cells'}}
        end
        emenu.rowDataMap={[120]={[1]=false,[2]=entry}}
        selections=0;emenu.onRowChanged=function()end
        emenu.rowChanged=function(t,row)assert(t==120 and row==2);selections=selections+1 end
        request('text.entries',{start=2});prep();result=run()
    """)
    assert r.eval('result.status')=='completed'
    observed=retrieve(r,body(r))
    assert observed['rows'][0]['entry']=='energycells'
    r.globals().rowtoken=observed['rows'][0]['token']
    r.execute("request('text.select',{row=rowtoken});prepared=prep();result=run()")
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    r.execute('status();run()');assert r.eval('selections')==1


def check_campaign_mismatch_refuses_before_cleanup_allocation():
    r,_=runtime();r.execute("""
        before=#disposers
        ok=pcall(descriptor.attach,{globals=g,ffi=ffi,C=C,Helper={convertComponentIDs=function(p)return p end},
            capacity_profile='campaign_v1',capacity_revision='wrong',
            capacity_resolver=function()return {revision='right'}end,
            on_dispose=function(fn)disposers[#disposers+1]=fn end})
    """)
    assert r.eval('ok') is False and r.eval('#disposers==before') is True


def check_native_property_window_and_sidebar_through_actual_sdk():
    r,_=runtime();r.execute("""
        m.shown=false;emenu.name='MapMenu';emenu.shown=true;emenu.mode=nil
        emenu.mainFrame=eframe;emenu.sideBar=120;eframe.properties.layer=6;emenu.frames={[6]=110}
        emenu.panelMode=true;emenu.infoTableMode='objectlist';emenu.panelState={leftbar=true,leftmenu=true}
        etable.rows[1].rowdata='propertyowned';emenu.rowDataMap={[120]={[1]='propertyowned'}}
        button=etable.rows[1][1];button.type='button';button.properties.active=true
        button.handlers={onClick=function()error('wrong route')end}
        emenu.buttonScriptMap={{layer=6,tableobj=120,row=1,col=1,type='onClick',button=130,script=button.handlers.onClick}}
        emenu.onRowChanged=function()end;selections=0
        emenu.rowChanged=function(t,n)assert(t==120 and n==1);selections=selections+1 end
        request('property.open',{});prepared=prep();result=run()
    """)
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    assert r.eval('selections')==1
    r.execute("""
        emenu.infoFrame=eframe;eframe.properties.layer=4;emenu.frames={[4]=110}
        emenu.infoTable=120;emenu.infoTableMode='propertyowned';emenu.propertyMode='propertyall'
        etable.numcolumns=10;propertyrow={'property',42ULL,nil,0}
        etable.rows[1].rowdata=propertyrow;emenu.rowDataMap[120][1]=propertyrow
        for i=1,10 do etable.rows[1][i]={type='text',id=150+i,colspan=1,properties={text='Ship name'}}end
        request('property.observe',{start=1});prep();result=run()
    """)
    assert r.eval('result.status')=='completed'
    observed=retrieve(r,body(r))
    assert observed['domain']=='property' and observed['rows'][0]['component']=='42'
    assert not observed['rendered_pixels_verified']


def check_fixed_close_through_sdk():
    r,_=runtime();r.execute("""
        show_text();closed=0;emenu.onCloseElement=function(reason)assert(reason=='close');closed=closed+1 end
        request('view.close',{domain='encyclopedia'});prepared=prep();result=run()
    """)
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    r.execute('run();status()');assert r.eval('closed')==1


@pytest.mark.parametrize('profile',['interactive','campaign_v1'])
def check_actual_capacity_observation_self_charge_and_cached_status(profile):
    r,_=runtime(profile=profile)
    r.execute("request('capacity_observe',{});prep();result=run();oldid=req.id;olddigest=req.digest")
    out=body(r)
    assert out['provider_history_used']==1 and out['includes_this_provider_request']
    assert out['provider_history_limit']==(256 if profile=='interactive' else 8192)
    assert out['navigation']['history_limit']==(128 if profile=='interactive' else 512)
    assert out['navigation']['history_used']==out['navigation']['windows_used']==0
    assert out['revision']==r.eval('capacity_revision') and len(out['source_sha256'])==64
    assert not out['current_counts_verified'] and out['native_operation_records_allocated']==0
    assert r.eval('queries+writes+converted+property_reads+context_reads')==0
    r.execute("request('capacity_observe',{});prep();result=run()")
    assert body(r)['provider_history_used']==2
    r.execute("result=registry.status(oldid,stamp.session,olddigest)")
    assert body(r)==out
    assert len(r.eval('wire.encode({payload=wire.encode(result)})').encode())<=4096


def check_capacity_arguments_are_closed():
    r,_=runtime();r.execute("request('capacity_observe',{profile='campaign_v1'});result=prep()")
    assert r.eval('result.status')!='prepared'
    assert r.eval('queries+writes+converted')==0


def actors(r):
    r.execute('''
      actorwrites=0
      C.GetPlayerID=function()return 1ULL end;C.GetPlayerContainerID=function()return 30ULL end
      C.GetContextByClass=function(v,k,include)assert(k=='room' and include==false);return 20ULL end
      C.IsComponentClass=function(v,k)return k=='npc' end
      C.IsConversationActive=function()return false end;C.IsConversationCancelling=function()return false end
      g.ConvertStringTo64Bit=function(v)return u(v:match('^(%d+)'))end
      g.GetPrioritizedPlatformNPCs=function(v)assert(v==20ULL);return {42ULL}end
      C.GetSofttarget2=function()return ffi.new('SofttargetDetails2',42ULL,'',7)end
      C.GetMessageDetails3=function()return ffi.new('MessageDetails3',0,42ULL,'npc','',false,false,false,true,false)end
      C.GetNumCompSlotPlayerActions=function()return 1 end
      C.GetCompSlotPlayerActions=function(b,n,s)
        assert(n==1 and s.component==42ULL);b[0].id=5;b[0].text='Comm';b[0].type='comm'
        b[0].ispossible=true;b[0].istobedisplayed=true;return 1
      end
      C.PerformCompSlotPlayerAction=function(s,id)assert(s.component==42ULL and id==5);actorwrites=actorwrites+1;return true end
      C.SetSofttarget=function(v,c)assert(v==42ULL and c=='');actorwrites=actorwrites+1;return true end
      g.GetActionInfo=function()return nil,true,1,true,nil,nil,1 end
      g.PerformAction=function(m,id)assert(m==7 and id==1);actorwrites=actorwrites+1 end
      request('actor.observe',{start=1});prep();result=run()
    ''')
    assert r.eval('result.status')=='completed'
    observed=retrieve(r,body(r))
    assert observed['native_count']==1 and not observed['complete_actor_census']
    r.globals().actor=observed['rows'][0]['actor']
    return observed


@pytest.mark.parametrize('action',['actor.focus','actor.comm','actor.interact'])
def check_actor_entry_actual_generated_adapter_sdk_wire_once(action):
    r,_=runtime();observed=actors(r)
    assert observed['rows'][0]['info']['owner']=='synthetic'
    r.globals().action=action
    r.execute("request(action,{actor=actor});prepared=prep();result=run()")
    assert r.eval('prepared.status')=='prepared' and r.eval('result.status')=='running'
    assert body(r)['native_status']=='DISPATCHED_UNVERIFIED'
    assert not body(r)['conversation_verified']
    assert len(r.eval('wire.encode({payload=wire.encode(result)})').encode())<=4096
    r.execute('run();status()');assert r.eval('actorwrites')==1


def check_actor_missing_core_refuses_without_changing_comm_or_other_actions():
    r,_=runtime();actors(r);r.execute("g.GetActionInfo=nil;request('actor.interact',{actor=actor});result=prep()")
    assert r.eval('result.status')=='refused' and r.eval('actorwrites')==0
    r.execute("request('actor.comm',{actor=actor});prep();result=run()")
    assert r.eval('result.status')=='running' and r.eval('actorwrites')==1


def check_actor_capacity_and_empty_arguments_remain_actual_and_closed():
    r,_=runtime();actors(r)
    r.execute("request('capacity_observe',{});prep();result=run()")
    out=body(r);assert out['actor']['requests_used']==0 and out['actor']['observations_used']==1
    r.execute("request('actor.comm',{actor='42',action_id=5});result=prep()")
    assert r.eval('result.status')=='refused' and r.eval('actorwrites')==0


def _cases():
    out=[]
    for name,fn in list(globals().items()):
        if not name.startswith('check_') or not callable(fn):continue
        marks=[m for m in getattr(fn,'pytestmark',[]) if m.name=='parametrize']
        cases=[{}]
        for mark in marks:
            key=mark.args[0];assert ',' not in key
            cases=[{**a,key:v} for a in cases for v in mark.args[1]]
        for i,args in enumerate(cases):out.append(pytest.param(name,args,id=f'{name}-{i}'))
    return out


@pytest.mark.parametrize('name,args',_cases())
def test_actual_luajit_control(name,args):
    result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),name,json.dumps(args)],capture_output=True,text=True,timeout=20,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,f'child exit {result.returncode}\n'+result.stdout+result.stderr[-6000:]
    assert result.stdout.strip()=='CONTROL_PASSED',result.stdout+result.stderr


if __name__=='__main__':
    name=sys.argv[1];assert name.startswith('check_') and callable(globals()[name])
    globals()[name](**json.loads(sys.argv[2]));print('CONTROL_PASSED')

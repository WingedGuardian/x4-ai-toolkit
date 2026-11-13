"""Actual Lua assembly and all real adapter constructors; no native game calls."""
from pathlib import Path

import pytest

luajit = pytest.importorskip("lupa.luajit21")
UI = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui"


def source(name):
    return (UI / ("live_" + name + ".lua")).read_text(encoding="utf-8")


@pytest.fixture
def runtime():
    rt = luajit.LuaRuntime(unpack_returned_tuples=True)
    rt.execute("""
        ffi=require('ffi');calls=0;disposed={};constructed={};last_read=nil
        current={provider='x4-live-session-v1',revision=string.rep('a',64),
            session=string.rep('b',32),generation=string.rep('c',32),world_token='world'}
        C=setmetatable({},{__index=function(t,k)
            local f=function(...) calls=calls+1;error('unexpected native call: '..k) end
            rawset(t,k,f);return f end})
        g={Menus={}}
        for _,name in ipairs({'GetCellContent','OpenMenu','ConvertIDTo64Bit','IsValidWidgetElement',
            'IsButtonActive','IsValidComponent','GetComponentData','ConvertStringToLuaID','SetOrderParam',
            'GetOrderParams','IsDockingPossible','GetPlayerMoney','GetOwnLicences','HasLicence','ConvertStringTo64Bit',
            'GetActiveGuidanceMissionComponent','GetAutoPilotTarget','GetPlayerActivity','getElapsedTime'}) do
            g[name]=function(...) calls=calls+1;error('unexpected native global: '..name) end
        end
        g.getElapsedTime=function()return 50 end
        function value(name,...)
            last_read={name,...};return {status='OBSERVED',kind=name}
        end
        function fake(name)
            local budgets={tickets={used=0,limit=128}}
            if name=='orders' then budgets={submissions={used=0,limit=128},cancellations={used=0,limit=128}}
            elseif name=='equipmentmods' then budgets={reads={used=0,limit=4096}}
            elseif name=='menu_pages' or name=='state' or name=='tasks' or name=='people' then
                budgets={attempts={used=0,limit=128}} end
            return {prepare=function() end,prepare_open=function() end,execute=function() end,
                capacity=function() return {available=true,budgets=budgets} end,
                prepare_cancel=function() end,execute_cancel=function() end,restore_cockpit=function() end,
                observe=function(...) return value(name,...) end,
                read=function(...) return value(name..'_read',...) end,
                capture=function(...) return value(name..'_capture',...) end,
                page=function(...) return value(name..'_page',...) end,
                metadata=function(...) return value(name..'_metadata',...) end,
                task=function(...) return value(name..'_task',...) end,
                dispose=function()
                    disposed[name]=(disposed[name] or 0)+1
                    if disposal_false then return false end
                end}
        end
        modules={
            menu={native_reader=function(...) return function() end,function() end end,
                new=function(d) constructed.menu=d;return fake('menu') end},
            orders=function(d) constructed.orders=d;return fake('orders') end,
            camera=function(d) constructed.camera=d;return fake('camera') end,
            transactions={new=function(d) constructed.transactions=d;return fake('transactions') end},
            playerstate=function(d) constructed.playerstate=d;return fake('state') end,
            buildtasks=function(d) constructed.buildtasks=d;return fake('tasks') end,
            dialogprobe=function(d) constructed.dialog=d;return fake('dialog') end,
            paths=function(d) constructed.paths=d;return fake('paths') end,
            logbook=function(d) constructed.logbook=d;return fake('logbook') end,
            camerastate=function(d) constructed.camerastate=d;return fake('camerastate') end,
            people=function(d) constructed.people=d;return fake('people') end,
            equipmentmods=function(d) constructed.equipmentmods=d;return fake('equipmentmods') end,
            ticker=function(d) constructed.ticker=d;return fake('ticker') end,
            options=function(d) constructed.options=d;return fake('options') end,
            playernavigation=function(d) constructed.navigation=d;return fake('navigation') end,
            timecontrol=function(d) constructed.timecontrol=d;return fake('timecontrol') end,
            menupages=function(d) constructed.menupages=d;return fake('menu_pages') end,
            nativeproviders=function(d)
                constructed.provider=d
                return {spec={id='x4-native-v1',revision=d.revision,actions={['camera.view']=true},
                    session=function() return current and current.session end,invoke=function() end},
                    session=function() return current and current.session end,
                    observe=function(...) return value('provider',...) end,
                    dispose=function() disposed.provider=(disposed.provider or 0)+1;return true end}
            end}
        deps={modules=modules,ffi=ffi,C=C,globals=g,Helper={convertComponentIDs=function(p) calls=calls+1;return p end},
            public_current=function() return current end,resolve_issued=function(token,kind) return ffi.new('uint64_t',42) end,
            clock=function() return 10 end,encode=function(result) last_encoded=result;return 'encoded' end,
            build=string.rep('f',64)}
    """)
    rt.globals().module = rt.execute(source("nativeassembly"))
    return rt


def construct(rt):
    rt.execute("assembly=module.new(deps)")
    return rt.globals().assembly


def test_metadata_does_not_construct_or_call_native(runtime):
    runtime.execute("first=module.read_verbs();first.orders=nil;second=module.read_verbs()")
    assert runtime.eval("second.orders") is True
    assert runtime.eval("next(constructed)") is None
    assert runtime.eval("calls") == 0


def test_lazy_construction_and_revision_requirement(runtime):
    a = construct(runtime)
    assert runtime.eval("calls") == 0
    assert a.provider_revisions["x4-native-v1"] == "f" * 64
    assert a.provider_spec.revision == "f" * 64
    assert a.abi_status == "SOURCE_DECLARED_PARAMETER_ABI_RUNTIME_UNVERIFIED"


@pytest.mark.parametrize("verb,args,name", [
    ("menu_capture", "{string.rep('1',32)}", "menu_pages_capture"),
    ("menu_page", "{string.rep('1',32),'0'}", "menu_pages_page"),
    ("orders", "{'ship'}", "provider"),
    ("transactions", "{'station'}", "provider"),
    ("state_capture", "{'peer',string.rep('1',32)}", "state_capture"),
    ("state_page", "{'playerstate:'..string.rep('1',32),'licences','0'}", "state_page"),
    ("buildtasks_capture", "{'station',string.rep('1',32)}", "tasks_capture"),
    ("buildtasks_page", "{'buildtasks:'..string.rep('1',32),'0'}", "tasks_page"),
    ("buildtasks_task", "{'buildtasks:'..string.rep('1',32),'123'}", "tasks_task"),
    ("dialog_probe", "{}", "dialog_capture"),
    ("native_save_path", "{}", "paths_read"),
    ("logbook_window", "{'1','16'}", "logbook_read"),
    ("ticker_window", "{'1','16'}", "ticker_read"),
    ("options_view", "{'main'}", "options"),
    ("player_navigation", "{'ship','target'}", "navigation"),
    ("time_state", "{'ship'}", "timecontrol"),
    ("camera_state", "{}", "camerastate_read"),
    ("people_capture", "{'ship',string.rep('1',32)}", "people_capture"),
    ("people_page", "{'people:capture','0'}", "people_page"),
    ("people_metadata", "{'people:capture'}", "people_metadata"),
    ("equipment_mods", "{'ship','engine'}", "equipmentmods_read"),
    ("equipment_mods", "{'ship','weapon','component'}", "equipmentmods_read"),
])
def test_exact_closed_read_routes(runtime, verb, args, name):
    construct(runtime)
    status, payload = runtime.execute(f"return assembly.read('{verb}',{args})")
    assert status == "OK" and payload == "encoded"
    assert runtime.eval("last_read[1]") == name


@pytest.mark.parametrize("verb,args", [
    ("eval", "{}"), ("orders", "{}"), ("orders", "{'ship','extra'}"), ("dialog_probe", "{'extra'}"),
    ("native_save_path", "{'extra'}"),
    ("logbook_window", "{'1'}"), ("logbook_window", "{'01','2'}"),
    ("ticker_window", "{'1'}"), ("ticker_window", "{'01','2'}"),
    ("options_view", "{}"), ("options_view", "{'main','extra'}"),
    ("player_navigation", "{'ship'}"), ("player_navigation", "{'ship','target','extra'}"),
    ("time_state", "{}"), ("time_state", "{'ship','extra'}"),
    ("camera_state", "{'extra'}"),
    ("people_capture", "{'ship','bad-nonce'}"), ("people_page", "{'id','-1'}"),
    ("people_metadata", "{'id','extra'}"), ("equipment_mods", "{'ship','engine','extra'}"),
    ("equipment_mods", "{'ship','weapon'}"),
    ("orders", "{[2]='ship'}"), ("orders", "{42}"),
    ("menu_capture", "{'short'}"), ("menu_page", "{string.rep('1',32),'01'}"),
    ("menu_page", "{string.rep('1',32),'1.0'}"),
    ("state_page", "{'capture','arbitrary','0'}"),
    ("buildtasks_page", "{'capture','99999999999999999999999'}"),
])
def test_malformed_read_never_dispatches(runtime, verb, args):
    construct(runtime)
    status, _ = runtime.execute(f"return assembly.read('{verb}',{args})")
    assert status == "ERR"
    assert runtime.eval("last_read") is None
    assert runtime.eval("calls") == 0


@pytest.mark.parametrize("mutation", [
    "deps.globals.OpenMenu=nil", "deps.Helper.convertComponentIDs=nil",
    "deps.modules.orders=nil", "deps.build='unknown'",
    "deps.C.EnableOrder=false", "deps.C.SetSofttarget=ffi.new('uint64_t',1)",
])
def test_missing_dependency_or_uncallable_symbol_blocks_factory(runtime, mutation):
    runtime.execute(mutation)
    okay, _ = runtime.execute("return pcall(module.new,deps)")
    assert not okay
    assert runtime.eval("calls") == 0


def test_disposal_retires_provider_and_read_caches_without_native_cleanup(runtime):
    a = construct(runtime)
    assert a.dispose() is True
    assert a.dispose() is True
    assert runtime.eval("disposed.provider") == 1
    assert runtime.eval("disposed.state") == 1
    assert runtime.eval("disposed.tasks") == 1
    assert runtime.eval("disposed.paths") == 1
    status, _ = runtime.execute("return assembly.read('orders',{'ship'})")
    assert status == "ERR"
    assert runtime.eval("calls") == 0


def test_failed_retirement_stays_failed_and_does_not_repeat_cleanup(runtime):
    construct(runtime)
    runtime.execute("disposal_false=true")
    first, _ = runtime.execute("return pcall(assembly.dispose)")
    assert not first
    runtime.execute("disposal_false=false")
    second, _ = runtime.execute("return pcall(assembly.dispose)")
    assert not second
    assert runtime.eval("disposed.provider") == 1
    assert runtime.eval("disposed.state") == 1
    assert runtime.eval("disposed.tasks") == 1
    assert runtime.eval("calls") == 0


def test_oversized_result_is_explicit_error(runtime):
    runtime.execute("deps.encode=function() return string.rep('x',32001) end")
    construct(runtime)
    status, payload = runtime.execute("return assembly.read('transactions',{'station'})")
    assert status == "ERR" and "wire limit" in payload


def test_payload_refusal_not_converted_into_acceptance(runtime):
    runtime.execute("modules.playerstate=function() local t=fake('state');t.capture=function() return {status='REFUSED',complete=false} end;return t end")
    construct(runtime)
    status, _ = runtime.execute("return assembly.read('state_capture',{'peer',string.rep('1',32)})")
    assert status == "OK"  # Transport only; inner refusal preserved for the reader.
    assert runtime.eval("last_encoded.status") == "REFUSED"
    assert runtime.eval("last_encoded.complete") is False


def test_native_save_path_uses_actual_observer_and_preserves_its_evidence(runtime):
    runtime.globals().modules.paths=runtime.execute(source('paths'))
    runtime.execute("pathbuf=ffi.new('char[?]',64,'/synthetic/profile/save');C.GetSaveFolderPath=function() calls=calls+1;return ffi.cast('const char *',pathbuf) end")
    construct(runtime)
    assert runtime.eval('calls')==0
    assert runtime.execute("return assembly.read('native_save_path',{})")==('OK','encoded')
    assert runtime.eval('last_encoded.scope')=='NATIVE_SAVE_FOLDER_PATH'
    assert runtime.eval('last_encoded.save_folder')=='/synthetic/profile/save'
    assert runtime.eval('last_encoded.stamp.generation')=='c'*32
    assert runtime.eval('calls')==1


def test_native_path_unavailable_remains_nonanswer_through_assembly(runtime):
    runtime.globals().modules.paths=runtime.execute(source('paths'))
    construct(runtime)
    assert runtime.execute("return assembly.read('native_save_path',{})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='UNAVAILABLE'
    assert runtime.eval('last_encoded.save_folder') is None


def test_actual_logbook_window_and_unavailability_survive_assembly(runtime):
    runtime.globals().modules.logbook=runtime.execute(source('logbook'))
    runtime.execute("g.GetNumLogbook=function(category) assert(category=='all');calls=calls+1;return 0 end")
    runtime.execute("g.GetLogbook=function() error('zero total must not fetch') end")
    construct(runtime)
    assert runtime.eval('calls')==0
    assert runtime.execute("return assembly.read('logbook_window',{'1','16'})")==('OK','encoded')
    assert runtime.eval('last_encoded.scope')=='NATIVE_ALL_LOGBOOK_WINDOW'
    assert runtime.eval('last_encoded.returned_count')==0
    assert runtime.eval('last_encoded.covers_total') is True
    assert runtime.eval('calls')==2
    runtime.execute('g.GetNumLogbook=function()return nil end')
    assert runtime.execute("return assembly.read('logbook_window',{'1','16'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='UNAVAILABLE'
    assert runtime.eval('last_encoded.rows') is None


def test_actual_camera_observer_scope_and_unavailability_survive_assembly(runtime):
    runtime.globals().modules.camerastate=runtime.execute(source('camerastate'))
    construct(runtime)
    runtime.execute("C.GetCameraRotation=function()return ffi.new('Rotation',{1,2,3})end;C.GetFollowCameraBasePos=function()return ffi.new('Coord3D',{4,5,6})end;C.IsExternalViewActive=function()return true end;C.GetExternalViewRefObject=function()return 18446744073709551615ULL end")
    assert runtime.execute("return assembly.read('camera_state',{})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.external_reference_handle')=='18446744073709551615'
    assert runtime.eval('last_encoded.achieved_pose_qualified') is False
    assert runtime.eval('last_encoded.target_distance_qualified') is False
    runtime.execute("C.GetCameraRotation=function()return nil end")
    assert runtime.execute("return assembly.read('camera_state',{})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='UNAVAILABLE'
    assert runtime.eval('last_encoded.rotation_native') is None


def test_actual_ticker_empty_window_and_failure_through_assembly(runtime):
    runtime.globals().modules.ticker=runtime.execute(source('ticker'))
    runtime.execute("C.GetNumTickerCache=function(category)assert(category=='');calls=calls+1;return 0 end")
    construct(runtime)
    assert runtime.eval('calls')==0
    assert runtime.execute("return assembly.read('ticker_window',{'1','16'})")==('OK','encoded')
    assert runtime.eval('last_encoded.scope')=='NATIVE_TICKER_CACHE_WINDOW'
    assert runtime.eval('last_encoded.returned_count')==0
    assert runtime.eval('last_encoded.covers_total') is True
    assert runtime.eval('last_encoded.rendered_notification_verified') is False
    assert runtime.eval('calls')==2
    runtime.execute('C.GetNumTickerCache=function()return nil end')
    assert runtime.execute("return assembly.read('ticker_window',{'1','16'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='UNAVAILABLE'
    assert runtime.eval('last_encoded.rows') is None


def test_actual_options_menu_view_and_wrong_page_through_assembly(runtime):
    runtime.globals().modules.options=runtime.execute(source('options'))
    runtime.execute("""
        rowdata={id='simple_menu_extension_options',submenu='simple_menu_extension_options',name='Extension Options'}
        row={index=1,rowdata=rowdata,properties={interactive=true}}
        native_table={type='table',id=20,rows={row}}
        options_frame={id=10,properties={layer=4},content={native_table}}
        g.Menus={{name='OptionsMenu',isStartmenu=false,shown=true,minimized=false,currentOption='main',
            optionsFrame=options_frame,frames={[4]=10},optionTable=20,rowDataMap={[20]={[1]=rowdata}}}}
        g.IsValidWidgetElement=function(id)return id==10 or id==20 end
    """)
    construct(runtime)
    assert runtime.execute("return assembly.read('options_view',{'main'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.submenus[1].id')=='simple_menu_extension_options'
    assert runtime.eval('last_encoded.setting_effect_verified') is False
    assert runtime.eval('constructed.provider.options==nil') is False
    assert runtime.execute("return assembly.read('options_view',{'other'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='REFUSED'
    assert runtime.eval('last_encoded.action_attempted') is False
    assert runtime.eval('calls')==0


def test_actual_player_navigation_through_native_id_wrappers(runtime):
    runtime.globals().modules.playernavigation=runtime.execute(source('playernavigation'))
    runtime.execute("""
        deps.resolve_issued=function(token,kind)
            if token=='ship'then assert(kind=='ship');return 42ULL end
            assert(token=='target' and kind=='object');return 43ULL
        end
        g.ConvertStringToLuaID=function(s)return s end
        g.ConvertIDTo64Bit=function(s)return tonumber(s:match('^(%d+)ULL$')) end
        g.ConvertStringTo64Bit=function(s)return tonumber(s:match('^(%d+)ULL$')) end
        g.IsValidComponent=function(n)assert(type(n)=='number');return n==42 or n==43 end
        g.GetComponentData=function(s,k)assert(s=='42ULL');return false end
        g.IsDockingPossible=function(s,t)assert(s=='42ULL' and t=='43ULL');return true end
        g.GetActiveGuidanceMissionComponent=function()return '43ULL'end
        g.GetAutoPilotTarget=function()return '0ULL'end
        C.GetPlayerControlledShipID=function()return 42ULL end
        C.GetPlayerOccupiedShipID=function()return 42ULL end
        C.IsComponentClass=function(id,k)return id==43ULL and k=='station'end
        C.GetContextByClass=function(id,k,include)if id==42ULL then return 0ULL end;return id end
        C.IsAutoPilotActive=function()return false end
        C.GetSofttarget2=function()return{softtargetID=43ULL}end
        C.GetEnvironmentObject=function()return 0ULL end
        C.RequestDockAt=function(id,check)assert(id==43ULL and check);return true end
        C.ToggleAutoPilot=function(check)assert(check);return true end
    """)
    construct(runtime)
    assert runtime.execute("return assembly.read('player_navigation',{'ship','target'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.guidance')=='43ULL'
    assert runtime.eval('last_encoded.quick_dock_target')=='43ULL'
    assert runtime.eval('last_encoded.arrival_verified') is False
    runtime.execute("C.GetPlayerControlledShipID=function()return 0ULL end")
    assert runtime.execute("return assembly.read('player_navigation',{'ship','target'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='REFUSED'


def test_real_all_adapter_constructors_do_not_query_or_write(runtime):
    for name in ("menu", "orders", "camera", "transactions", "nativeproviders",
                 "menupages", "playerstate", "buildtasks", "dialogprobe", "paths", "logbook", "camerastate", "people", "equipmentmods", "ticker", "options", "playernavigation", "timecontrol"):
        runtime.globals().modules[name] = runtime.execute(source(name))
    construct(runtime)
    assert runtime.eval("calls") == 0
    assert runtime.eval("assembly.provider_spec.id") == "x4-native-v1"
    assert runtime.eval("assembly.dispose()") is True


def test_actual_people_capture_pages_and_refusal_through_native_wrapper(runtime):
    runtime.globals().modules.people=runtime.execute(source('people'))
    runtime.execute("g.ConvertStringTo64Bit=function()return 42ULL end;g.IsValidComponent=function()return true end;C.IsComponentClass=function()return true end;C.GetPeopleCapacity=function()return 10 end;C.GetNumAllRoles=function()return 0 end;C.GetPeople2=function()return 0 end")
    construct(runtime)
    assert runtime.execute("return assembly.read('people_capture',{'ship',string.rep('1',32)})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.total')==0
    assert runtime.eval('last_encoded.counts.people')==0
    assert runtime.eval('last_encoded.include_arriving') is True
    runtime.execute('capture_id=last_encoded.capture_id')
    assert runtime.execute("return assembly.read('people_page',{capture_id,'0'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    runtime.execute('C.GetNumAllRoles=function()return nil end')
    assert runtime.execute("return assembly.read('people_capture',{'ship',string.rep('2',32)})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='REFUSED'
    assert runtime.eval('last_encoded.complete') is False


def test_actual_mod_absence_and_unknown_are_distinct_through_native_wrapper(runtime):
    runtime.globals().modules.equipmentmods=runtime.execute(source('equipmentmods'))
    runtime.execute("g.ConvertStringTo64Bit=function()return 42ULL end;g.IsValidComponent=function()return true end;C.IsComponentClass=function()return true end;C.GetInstalledEngineMod2=function()return false end")
    construct(runtime)
    assert runtime.execute("return assembly.read('equipment_mods',{'ship','engine'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.target.installed') is False
    assert runtime.eval('last_encoded.fitting_census') is False
    runtime.execute('C.GetInstalledEngineMod2=function()return nil end')
    assert runtime.execute("return assembly.read('equipment_mods',{'ship','engine'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='REFUSED'
    assert runtime.eval('last_encoded.target') is None


def test_actual_time_state_uses_native_activity_and_retains_clock_domains(runtime):
    runtime.globals().modules.timecontrol=runtime.execute(source('timecontrol'))
    runtime.execute('''
        ui_time=50
        g.getElapsedTime=function()return ui_time end
        g.GetPlayerActivity=function() return 'seta' end
        C.GetPlayerOccupiedShipID=function()return 42ULL end
        C.GetCurrentGameTime=function()return 123 end
        C.HasSeta=function()return true end
        C.CanActivateSeta=function(v)assert(v==false);return false end
        C.IsSetaActive=function()return true end
    ''')
    construct(runtime)
    assert runtime.execute("return assembly.read('time_state',{'ship'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='OBSERVED'
    assert runtime.eval('last_encoded.activity')=='seta'
    assert runtime.eval('last_encoded.game_time')==123
    assert runtime.eval('last_encoded.ui_begin')==50
    assert runtime.eval('last_encoded.ui_end')==50
    assert runtime.eval('last_encoded.acceleration_factor_qualified') is False
    runtime.execute("C.GetPlayerOccupiedShipID=function()return 43ULL end")
    assert runtime.execute("return assembly.read('time_state',{'ship'})")==('OK','encoded')
    assert runtime.eval('last_encoded.status')=='REFUSED'


def test_time_ticket_expires_on_elapsed_clock_while_game_clock_is_frozen(runtime):
    runtime.globals().modules.timecontrol=runtime.execute(source('timecontrol'))
    runtime.execute('''
        ui_time=50;time_writes=0
        g.getElapsedTime=function()return ui_time end
        g.GetPlayerActivity=function()return '' end
        C.GetPlayerOccupiedShipID=function()return 42ULL end
        C.GetCurrentGameTime=function()return 123 end
        C.HasSeta=function()return true end
        C.CanActivateSeta=function()return true end
        C.IsSetaActive=function()return false end
        C.StartPlayerActivity=function()time_writes=time_writes+1 end
    ''')
    construct(runtime)
    runtime.execute("ticket=constructed.provider.timecontrol.prepare('start','ship');ui_time=56")
    assert runtime.eval('ticket.status')=='PREPARED'
    assert runtime.eval('constructed.provider.timecontrol.execute(ticket.ticket).status')=='REFUSED'
    assert runtime.eval('time_writes')==0


def test_ui_labeled_observers_receive_elapsed_time_independently(runtime):
    construct(runtime)
    for name in ('ticker','navigation','camerastate','timecontrol'):
        assert runtime.eval(f'constructed.{name}.now()')==50
    assert runtime.eval('deps.clock()')==10
    assert runtime.eval('constructed.options.now()')==10


def test_native_wrapper_argument_binding(runtime):
    construct(runtime)
    runtime.execute("g.Menus={value='new'};r=constructed.menu.menus()")
    assert runtime.eval("r.value") == "new"
    runtime.execute("s=constructed.transactions.resolve_issued_station('station')")
    assert runtime.eval("tostring(s)") == "42ULL"
    assert runtime.eval("constructed.orders.epoch()") == "b" * 32 + ":" + "c" * 32


def converters(rt):
    rt.execute("""
        g.ConvertStringToLuaID=function(v) return 'lua:'..v end
        g.ConvertIDTo64Bit=function(v) return converted_number or 42 end
        g.ConvertStringTo64Bit=function(v) return converted_number or 42 end
        g.GetComponentData=function(v,...) calls=calls+1;assert(v=='lua:42ULL');return nil,true,'last' end
        g.GetOrderParams=function(v,index) calls=calls+1;assert(v=='lua:42ULL' and index==2);return {} end
        g.SetOrderParam=function(v,index,...) calls=calls+1;assert(v=='lua:42ULL' and index==2) end
        g.IsDockingPossible=function(a,b,...) calls=calls+1;assert(a=='lua:42ULL' and b=='lua:42ULL');return true end
        g.IsValidComponent=function(v) calls=calls+1;assert(type(v)=='number' and v==42);return true end
    """)


def test_bare_globals_receive_native_converted_ids_and_preserve_nil_returns(runtime):
    converters(runtime)
    construct(runtime)
    assert runtime.eval("calls") == 0
    assert runtime.execute("return constructed.orders.globals.GetComponentData(ffi.new('uint64_t',42),'a','b','c')") == (None, True, "last")
    runtime.execute("constructed.orders.globals.GetOrderParams(ffi.new('uint64_t',42),2);constructed.orders.globals.SetOrderParam(ffi.new('uint64_t',42),2,1,nil,'value')")
    assert runtime.eval("constructed.orders.globals.IsDockingPossible(ffi.new('uint64_t',42),ffi.new('uint64_t',42))") is True
    assert runtime.eval("constructed.camera.is_valid(ffi.new('uint64_t',42))") is True
    assert runtime.eval("calls") == 5


@pytest.mark.parametrize("bad", ["43", "9007199254740992", "-1", "1.5", "0/0"])
def test_native_conversion_mismatch_or_unsafe_integer_refuses_before_lookup(runtime, bad):
    converters(runtime)
    construct(runtime)
    runtime.execute(f"converted_number={bad}")
    okay, _ = runtime.execute("return pcall(constructed.orders.globals.GetComponentData,ffi.new('uint64_t',42),'owner')")
    assert not okay
    assert runtime.eval("calls") == 0


def test_native_id_normalization_keeps_exact_cdata_and_safe_numbers(runtime):
    converters(runtime)
    construct(runtime)
    assert runtime.eval("tostring(constructed.orders.globals.ConvertIDTo64Bit('engine-id'))") == "42ULL"
    runtime.execute("converted_number=9007199254740992")
    assert runtime.eval("tostring(constructed.orders.globals.ConvertIDTo64Bit(ffi.new('uint64_t',42)))") == "42ULL"
    okay, _ = runtime.execute("return pcall(constructed.orders.globals.ConvertIDTo64Bit,'engine-id')")
    assert not okay

def ownership_bindings(rt, value):
    # The synthetic Lua ID intentionally cannot be obtained by tonumber(uint64).
    rt.execute("""
        wide=ffi.cast('uint64_t',-1)
        wide_text=tostring(wide);roundtrip_value=wide
        g.ConvertStringToLuaID=function(v)
            assert(v==wide_text);return 'opaque-native-lua-id'
        end
        g.ConvertIDTo64Bit=function(v)
            assert(v=='opaque-native-lua-id');return roundtrip_value
        end
        g.GetComponentData=function(v,key)
            calls=calls+1
            assert(v=='opaque-native-lua-id' and key=='isplayerowned')
            return ownership
        end
    """)
    rt.execute("ownership=" + value)


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_menu_ownership_forwarding_uses_exact_native_conversion(runtime, value, expected):
    ownership_bindings(runtime, value)
    construct(runtime)
    assert runtime.eval("calls") == 0
    assert runtime.eval("constructed.menu.is_player_owned(wide)") is expected
    assert runtime.eval("calls") == 1


@pytest.mark.parametrize("value", ["nil", "0", "1", "'true'", "{}"])
def test_menu_ownership_nonboolean_is_unavailable_not_false(runtime, value):
    ownership_bindings(runtime, value)
    construct(runtime)
    okay, _ = runtime.execute("return pcall(constructed.menu.is_player_owned,wide)")
    assert not okay
    assert runtime.eval("calls") == 1


def test_menu_ownership_conversion_drift_and_disposal_refuse_before_lookup(runtime):
    ownership_bindings(runtime, "true")
    assembly = construct(runtime)
    runtime.execute("roundtrip_value=ffi.new('uint64_t',43)")
    okay, _ = runtime.execute("return pcall(constructed.menu.is_player_owned,ffi.cast('uint64_t',-1))")
    assert not okay
    assert runtime.eval("calls") == 0
    assert assembly.dispose() is True
    okay, _ = runtime.execute("return pcall(constructed.menu.is_player_owned,wide)")
    assert not okay
    assert runtime.eval("calls") == 0

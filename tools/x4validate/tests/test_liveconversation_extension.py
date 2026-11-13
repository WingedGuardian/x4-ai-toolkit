"""Actual own-code loader + observer/provider/SDK; no native engine calls."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import hashlib
import pytest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('conversation_extension',ROOT/'scripts/live_conversation_extension.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)


def test_pure_factory_binds_exact_sources_and_unchanged_compiled_md():
    files,receipt=g.prepare()
    assert set(files)=={'md/X4ToolkitConversation.xml','ui/live_conversation_extension.lua'}
    assert hashlib.sha256(files['md/X4ToolkitConversation.xml']).hexdigest()=='2256d7c25a921f511c5dbb7ae6063daadb117558170ce3b3889d60e9d7e61ccc'
    assert receipt['runtime_verified'] is False and receipt['radial_selection']=='BLOCKED'
    assert receipt['comm_widget_route']=='SOURCE_SUPPORTED_OFFLINE_ONLY' and receipt['rendered_runtime']=='UNQUALIFIED'
    assert receipt['actor_context_navigation']=='BUILT_LEFT_OR_RIGHT_INFO_ROWS_ONLY_OFFLINE'
    assert receipt['map_navigation']=='FIXED_OPEN_AND_BUILT_PANEL_OBJECTINFO_WRAPPERS_OFFLINE'
    for path,digest in receipt['source_sha256'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest


def test_source_drift_during_pure_generation_refused():
    read=Path.read_bytes;count=0
    def changed(path):
        nonlocal count
        data=read(path)
        if path==g.ROOT/g.PATHS['observer']:
            count+=1
            if count>1:return data+b'\n-- changed\n'
        return data
    with patch.object(Path,'read_bytes',changed),pytest.raises(ValueError,match='input drift'):g.prepare()


def test_generated_loader_constructs_then_registers_actual_provider_without_snapshot():
    files,receipt=g.prepare();r=LuaRuntime(unpack_returned_tuples=True)
    r.globals().core=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actions.lua').read_text())
    r.execute("""
        ffi=require('ffi');callbacks={};sends=0;stamp={provider='x4-live-session-v1',revision=string.rep('a',64),
            session=string.rep('b',32),generation=string.rep('c',32),world_token='world'}
        channel={public_current=function() return stamp end,now=function() return 0 end,
            arm_tick=function(fn) tick=fn end,subscribe=function(fn) drain=fn end,
            send=function() sends=sends+1 end,read=function() return nil end,clear=function() end,
            component_id=function() error('unexpected native conversion') end,dispose=function() return true end}
        X4ToolkitAutomationBundle={register_extension=function(d) descriptor=d end}
    """)
    r.execute(files['ui/live_conversation_extension.lua'].decode())
    assert r.eval('descriptor.providers["x4-conversation-observer-v1"]')==receipt['revision']
    r.execute("""
        registry=core();ctx={public_current=function() return stamp end,registry=registry,ffi=ffi,
            Helper={convertComponentIDs=function()error('unexpected parameter conversion')end},
            globals={Menus={},ConvertIDTo64Bit=function() error('unexpected conversion') end,
                GetCellContent=function() error('unexpected widget') end,IsValidWidgetElement=function() error('unexpected widget') end,
                IsButtonActive=function() error('unexpected widget') end,GetLocalMousePosition=function() error('unexpected mouse read') end,
                OpenMenu=function()error('unexpected open')end},clock=function() return 0 end,
            resolve_issued=function() error('unexpected resolution') end,
            create_md_channel=function(config) assert(config.controls.snapshot and config.screen=='X4ToolkitConversation');return channel end,
            on_dispose=function(fn) callbacks[#callbacks+1]=fn end}
        attached=descriptor.attach(ctx)
        req={version=1,id=string.rep('d',32),session=stamp.session,provider='x4-conversation-observer-v1',
            action='snapshot',binding=descriptor.revision,digest=string.rep('e',64),arguments={}}
    """)
    assert r.eval('sends')==0 and r.eval('#callbacks')==6
    assert r.eval('registry.prepare(req).status')=='prepared' and r.eval('sends')==0
    assert r.eval('registry.execute(req.id,req.session,req.digest).status')=='running'
    assert r.eval('registry.status(req.id,req.session,req.digest).status')=='running' and r.eval('sends')==1
    r.execute('attached.dispose();for _,fn in ipairs(callbacks) do assert(fn()==true) end')
    assert r.eval('registry.status(req.id,req.session,req.digest).status')=='unknown'


def test_generated_loader_registers_and_dispatches_actual_comm_wrapper():
    # Reuse the literal known native model fixture, then consume this actual loader.
    from test_livecommmenu import runtime
    r=runtime();files,_=g.prepare()
    r.globals().core=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actions.lua').read_text())
    r.execute("""
        X4ToolkitAutomationBundle={register_extension=function(d) descriptor=d end}
        channel={public_current=function() return stamp end,now=function() return time end,
            arm_tick=function() end,subscribe=function() end,send=function() error('unexpected MD send') end,
            read=function() return nil end,clear=function() end,component_id=function() error('unexpected MD conversion') end,
            dispose=function() return true end}
    """)
    r.execute(files['ui/live_conversation_extension.lua'].decode())
    r.execute("""
        registry=core();callbacks={}
        ctx={public_current=d.public_current,ffi=ffi,clock=d.clock,resolve_issued=d.resolve_issued,
            Helper={convertComponentIDs=function()error('unexpected parameter conversion')end},
            globals={Menus=menus,GetCellContent=d.cell_id,ConvertIDTo64Bit=d.component_id,
                IsValidWidgetElement=function(id) return id==30 end,IsButtonActive=function() return native_active end,
                GetLocalMousePosition=function() return 1,2 end,OpenMenu=function()error('unexpected open')end},
            create_md_channel=function() return channel end,registry=registry,
            on_dispose=function(fn) callbacks[#callbacks+1]=fn end}
        attached=descriptor.attach(ctx)
        req={version=1,id=string.rep('1',32),session=stamp.session,provider='x4-comm-menu-v1',
            action='inspect',arguments={},binding=descriptor.revision,digest=string.rep('2',64)}
    """)
    assert r.eval('registry.prepare(req).status')=='prepared'
    inspected=r.eval('registry.execute(req.id,req.session,req.digest)')
    assert inspected.status=='completed' and inspected.observations.native.actor_context.entity=='43'
    r.execute("req.id=string.rep('3',32);req.digest=string.rep('4',64);req.action='open';req.arguments={context='context',kind='ENTITY',actor='43'}")
    assert r.eval('registry.prepare(req).status')=='prepared'
    result=r.eval('registry.execute(req.id,req.session,req.digest)')
    assert result.status=='running' and result.observations.wrapper_attempted is True
    assert result.observations.conversation_open_verified is False and r.eval('writes')==1


def test_generated_loader_dispatches_actual_actor_context_adapter_and_sdk():
    from test_liveactorcontext import runtime
    r=runtime();files,_=g.prepare()
    r.globals().core=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actions.lua').read_text())
    r.execute("""
        X4ToolkitAutomationBundle={register_extension=function(d) descriptor=d end}
        channel={public_current=function() return stamp end,now=function() return time end,
            arm_tick=function() end,subscribe=function() end,send=function() error('unexpected MD send') end,
            read=function() return nil end,clear=function() end,component_id=function() error('unexpected MD conversion') end,
            dispose=function() return true end}
    """)
    r.execute(files['ui/live_conversation_extension.lua'].decode())
    r.execute("""
        registry=core();callbacks={}
        ctx={public_current=d.public_current,ffi=ffi,clock=d.clock,resolve_issued=d.resolve_issued,
            Helper={convertComponentIDs=function()error('unexpected parameter conversion')end},
            globals={Menus=menus,GetCellContent=function() error('unexpected widget') end,ConvertIDTo64Bit=d.component_id,
                IsValidWidgetElement=d.table_valid,IsButtonActive=function() error('unexpected widget') end,
                GetLocalMousePosition=d.mouse_position,OpenMenu=function()error('unexpected open')end},
            create_md_channel=function() return channel end,registry=registry,
            on_dispose=function(fn) callbacks[#callbacks+1]=fn end}
        attached=descriptor.attach(ctx)
        req={version=1,id=string.rep('1',32),session=stamp.session,provider='x4-comm-menu-v1',
            action='context_inspect',arguments={},binding=descriptor.revision,digest=string.rep('2',64)}
    """)
    assert r.eval('registry.prepare(req).status')=='prepared'
    out=r.eval('registry.execute(req.id,req.session,req.digest)')
    assert out.status=='completed' and out.observations.native.rows[1].actor=='43'
    r.execute("req.id=string.rep('3',32);req.digest=string.rep('4',64);req.action='context_open';req.arguments={context='context',role='info_shiptrader',actor='43'}")
    assert r.eval('registry.prepare(req).status')=='prepared'
    out=r.eval('registry.execute(req.id,req.session,req.digest)')
    assert out.status=='running' and out.observations.context_handler_attempted is True
    assert out.observations.wrapper_attempted is False and out.observations.context_open_verified is False
    assert r.eval('writes')==1


def test_generated_loader_dispatches_actual_map_route():
    from test_livemapnavigation import runtime
    r=runtime();files,_=g.prepare()
    r.globals().core=r.execute((ROOT/'mods/x4_toolkit_helper/ui/live_actions.lua').read_text())
    r.execute("""
        ffi=require('ffi');X4ToolkitAutomationBundle={register_extension=function(d)descriptor=d end}
        channel={public_current=d.public_current,now=d.clock,arm_tick=function()end,subscribe=function()end,
            send=function()error('unexpected MD')end,read=function()return nil end,clear=function()end,
            component_id=function()error('unexpected MD conversion')end,dispose=function()return true end}
    """)
    r.execute(files['ui/live_conversation_extension.lua'].decode())
    r.execute("""
        registry=core();callbacks={}
        ctx={public_current=d.public_current,ffi=ffi,clock=d.clock,resolve_issued=d.resolve_issued,
            globals={Menus=menus,GetCellContent=d.cell_id,ConvertIDTo64Bit=d.component_id,
                IsValidWidgetElement=d.valid_widget,IsButtonActive=function()return active end,
                GetLocalMousePosition=function()return 1,2 end,OpenMenu=d.open_menu},
            Helper={convertComponentIDs=d.convert_parameters},create_md_channel=function()return channel end,
            registry=registry,on_dispose=function(fn)callbacks[#callbacks+1]=fn end}
        attached=descriptor.attach(ctx)
        req={version=1,id=string.rep('1',32),session=stamp.session,provider='x4-comm-menu-v1',action='map_objectinfo',
            arguments={context='context',side='left'},binding=descriptor.revision,digest=string.rep('2',64)}
    """)
    assert r.eval('registry.prepare(req).status')=='prepared'
    out=r.eval('registry.execute(req.id,req.session,req.digest)')
    assert out.status=='running' and out.observations.map_handler_attempted is True
    assert out.observations.info_open_verified is False and r.eval('writes')==1
    r.execute("m.shown=false;req.id=string.rep('3',32);req.digest=string.rep('4',64);req.action='map_open'")
    assert r.eval('registry.prepare(req).status')=='prepared'
    assert r.eval('registry.execute(req.id,req.session,req.digest).status')=='running'
    assert r.eval('writes')==2

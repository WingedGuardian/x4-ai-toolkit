"""Actual bundle construction/Lua load; no native bootstrap or game calls."""
import importlib.util
from pathlib import Path
import pytest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("automation_bundle",ROOT/"scripts/live_automation_bundle.py")
bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
REV="a"*64


def sources():
    return {key:(ROOT/path).read_bytes() for key,path in bundle.SOURCES.items()}


def synthetic():
    src={key:b"return function() return {} end" for key in bundle.MODULES}
    src["bootstrap"]=b"""return function(deps)
        captured=deps
        if fail_bootstrap then error('bootstrap creation failed') end
        local current={session={current=function() return {session='session'} end},
            encode=function() return 'encoded' end}
        local cleanups={}
        function current.register_provider(spec)
            if fail_register then error('registration failed') end
            registered=spec;all_registered=all_registered or {};all_registered[spec.id]=spec
            cleanups[#cleanups+1]=spec.dispose
        end
        function current.dispose()
            disposed=(disposed or 0)+1
            if fail_dispose then return false end
            for _,fn in ipairs(cleanups) do assert(fn()==true,'provider cleanup failed') end
            return true
        end
        current_bootstrap=current
        return current
    end"""
    src["nativeassembly"]=b"""return {
        read_verbs=function() return {orders=true,menu_capture=true} end,
        new=function(deps)
            native_captured=deps
            if fail_native then error('native creation failed') end
            local closed=false
            local result={read=function(verb,args) native_read=verb;return 'OK','native' end}
            function result.dispose()
                if fail_native_dispose then return false end
                if not closed then native_disposed=(native_disposed or 0)+1;closed=true end
                return true
            end
            result.provider_spec={id='x4-native-v1',revision=deps.build,actions={['camera.view']=true},
                session=function() return 'session' end,invoke=function() end,dispose=result.dispose}
            return result
        end}
    """
    src['mdchannel']=b"""return function(deps)
        channel_deps=deps
        return {dispose=function() channel_closed=(channel_closed or 0)+1;return true end}
    end"""
    src['startbootstrap']=b"""return {new=function(deps)
        ui_captured=deps;ui_created=(ui_created or 0)+1
        if ui_new_throws then error('UI construction failed') end
        return {start=function()
            ui_started=(ui_started or 0)+1
            if ui_start_throws then error('UI schedule failed') end
            return {status='SCHEDULED',world_session=false}
        end,dispose=function()
            ui_disposed=(ui_disposed or 0)+1
            if ui_dispose_callback then ui_dispose_callback() end
            return not ui_dispose_fails
        end}
    end}"""
    return src


def runtime(payload):
    r=LuaRuntime(unpack_returned_tuples=True);r.execute(payload.decode())
    r.execute("function deps() return {read=function() return 'OK','legacy' end,read_verbs={probe=true}} end")
    return r


def test_actual_module_bundle_loads_without_native_bootstrap():
    payload,receipt=bundle.build(sources(),REV)
    assert bundle.verify(payload)==receipt["build"]
    r=LuaRuntime(unpack_returned_tuples=True);r.execute(payload.decode())
    assert r.globals().X4ToolkitAutomationBundle.build==receipt["build"]
    assert r.globals().X4ToolkitAutomationBundle.revision==REV
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    assert receipt["runtime_verified"] is False


def ui_runtime():
    payload,receipt=bundle.build(synthetic(),REV);r=runtime(payload)
    r.execute("function uideps() return {C={IsStartmenu=function() return in_menu end},globals={},Helper={},pipes={}} end;in_menu=true")
    return r,receipt


def test_ui_bootstrap_uses_own_modules_and_loaded_build_once():
    r,receipt=ui_runtime()
    assert r.eval("X4ToolkitAutomationBundle.start_ui(uideps()).status")=="SCHEDULED"
    assert r.globals().ui_captured.build==receipt['build']
    assert r.globals().ui_captured.modules.codec is not None
    assert r.eval("pcall(X4ToolkitAutomationBundle.start_ui,uideps())")[0] is False
    assert r.globals().ui_created==1 and r.globals().ui_started==1


def test_world_entry_retires_ui_before_world_bootstrap():
    r,_=ui_runtime();r.execute("X4ToolkitAutomationBundle.start_ui(uideps());X4ToolkitAutomationBundle.new(deps())")
    assert r.globals().ui_disposed==1 and r.globals().captured is not None


def test_non_menu_skips_ui_construction():
    r,_=ui_runtime();r.execute("in_menu=false")
    assert r.eval("X4ToolkitAutomationBundle.start_ui(uideps()).status")=="NOT_START_MENU"
    assert r.globals().ui_created is None


@pytest.mark.parametrize('flag',['ui_new_throws','ui_start_throws'])
def test_ui_start_failure_is_sticky_without_retry(flag):
    r,_=ui_runtime();r.globals()[flag]=True
    assert r.eval("pcall(X4ToolkitAutomationBundle.start_ui,uideps())")[0] is False
    r.globals()[flag]=False
    assert r.eval("pcall(X4ToolkitAutomationBundle.start_ui,uideps())")[0] is False
    assert r.eval("pcall(X4ToolkitAutomationBundle.new,deps())")[0] is False
    assert r.globals().ui_created==1


def test_failed_ui_retirement_cannot_replace_with_world_owner():
    r,_=ui_runtime();r.execute("X4ToolkitAutomationBundle.start_ui(uideps());ui_dispose_fails=true")
    assert r.eval("pcall(X4ToolkitAutomationBundle.new,deps())")[0] is False
    r.execute("ui_dispose_fails=false")
    assert r.eval("pcall(X4ToolkitAutomationBundle.new,deps())")[0] is False
    assert r.globals().captured is None


@pytest.mark.parametrize('operation',["X4ToolkitAutomationBundle.new(deps())","X4ToolkitAutomationBundle.dispose()"])
def test_ui_retirement_cannot_reenter_bundle(operation):
    r,_=ui_runtime()
    r.execute("X4ToolkitAutomationBundle.start_ui(uideps());ui_dispose_callback=function() "+operation+" end")
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().ui_disposed==1 and r.globals().captured is None
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False


@pytest.mark.parametrize("key",bundle.MODULES)
def test_every_module_affects_loaded_identity(key):
    src=synthetic();before,_=bundle.build(src,REV)
    src[key]+=b"\n-- changed"
    after,_=bundle.build(src,REV)
    assert bundle.verify(before)!=bundle.verify(after)


def test_md_revision_affects_loaded_identity():
    a,_=bundle.build(synthetic(),REV);b,_=bundle.build(synthetic(),"b"*64)
    assert bundle.verify(a)!=bundle.verify(b)


def test_lf_and_crlf_sources_have_same_semantic_candidate():
    src=synthetic();a,_=bundle.build(src,REV)
    b,_=bundle.build({k:v.replace(b"\n",b"\r\n") for k,v in src.items()},REV)
    assert a==b


def test_tampering_cannot_keep_old_build():
    p,_=bundle.build(synthetic(),REV)
    with pytest.raises(ValueError):bundle.verify(p+b"\n--changed")


def test_bundle_overrides_caller_module_build_and_revision():
    p,receipt=bundle.build(synthetic(),REV)
    r=runtime(p)
    r.execute("d=deps();d.build='wrong';d.revision='wrong';d.modules={};X4ToolkitAutomationBundle.new(d)")
    assert r.globals().captured.build==receipt["build"] and r.globals().captured.revision==REV
    assert len(list(r.globals().captured.modules.keys()))==len(bundle.MODULES)


def test_replacement_retires_old_instance_and_old_factory():
    p,_=bundle.build(synthetic(),REV)
    r=runtime(p)
    r.execute("old=X4ToolkitAutomationBundle;old.new(deps())")
    r.execute(p.decode())
    assert r.globals().disposed==1
    assert r.eval("pcall(function() old.new(deps()) end)")[0] is False


def test_failed_disposal_stays_failed_and_does_not_replace_global():
    p,_=bundle.build(synthetic(),REV)
    r=runtime(p)
    r.execute("old=X4ToolkitAutomationBundle;old.new(deps());fail_dispose=true")
    assert r.eval("pcall(function() old.new(deps()) end)")[0] is False
    r.execute("fail_dispose=false")
    assert r.eval("pcall(old.dispose)")[0] is False
    assert r.eval("old==X4ToolkitAutomationBundle") is True


def test_missing_module_refuses():
    src=synthetic();del src["session"]
    with pytest.raises(ValueError):bundle.build(src,REV)


def test_native_registration_and_read_routing_are_mandatory():
    p,receipt=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute("X4ToolkitAutomationBundle.new(deps())")
    assert r.globals().registered.id=='x4-native-v1'
    assert r.globals().registered.revision==receipt['build']
    assert r.globals().captured.provider_revisions['x4-native-v1']==receipt['build']
    assert r.globals().captured.read_verbs.orders is True
    assert r.execute("return captured.read('orders',{})")==('OK','native')
    assert r.execute("return captured.read('probe',{})")==('OK','legacy')
    assert r.eval("native_captured.public_current==current_bootstrap.session.current") is True
    assert r.eval("native_captured.encode==current_bootstrap.encode") is True


@pytest.mark.parametrize('mutation',[
    "d.read_verbs.orders=true", "d.read_verbs.probe=false",
    "d.provider_revisions={['x4-native-v1']=string.rep('0',64)}",
])
def test_mixed_or_invalid_namespaces_refuse_before_bootstrap(mutation):
    p,_=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute('d=deps();'+mutation)
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,d)')[0] is False
    assert r.globals().captured is None


@pytest.mark.parametrize('flag',['fail_bootstrap','fail_native','fail_register'])
def test_constructor_or_registration_failure_is_sticky_no_fallback(flag):
    p,_=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute(flag+'=true')
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    r.execute(flag+'=false')
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.eval('pcall(X4ToolkitAutomationBundle.dispose)')[0] is False
    if flag!='fail_bootstrap': assert r.globals().disposed==1
    if flag=='fail_register': assert r.globals().native_disposed==1


def test_cleanup_failure_is_preserved_and_does_not_register_fallback():
    p,_=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute('fail_register=true;fail_native_dispose=true')
    okay,reason=r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')
    assert not okay and 'cleanup failed' in reason
    assert r.globals().registered is None
    r.execute('fail_register=false;fail_native_dispose=false')
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False


def test_successful_retirement_disposes_native_assembly():
    p,_=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute('X4ToolkitAutomationBundle.new(deps())')
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    assert r.globals().native_disposed==1


def test_later_caller_vocabulary_mutation_cannot_expand_read_namespace():
    p,_=bundle.build(synthetic(),REV);r=runtime(p)
    r.execute("d=deps();X4ToolkitAutomationBundle.new(d);d.read_verbs.eval=true;d.read_verbs.probe=nil")
    assert r.eval("pcall(captured.read,'eval',{})")[0] is False
    assert r.execute("return captured.read('probe',{})")==('OK','legacy')


def test_actual_bundle_bootstrap_and_native_assembly_construct_without_game_reads():
    luajit=pytest.importorskip('lupa.luajit21')
    payload,_=bundle.build(sources(),REV)
    r=luajit.LuaRuntime(unpack_returned_tuples=True)
    r.execute("""
        ffi=require('ffi');ffi.cdef('typedef uint64_t UniverseID;')
        native_calls=0;events={}
        local function forbidden() native_calls=native_calls+1;error('unexpected native data/action call') end
        held={ffi.cast('uint64_t (*)(void)',function() forbidden();return 0 end),
            ffi.cast('double (*)(void)',function() forbidden();return 0 end)}
        C=setmetatable({GetPlayerID=held[1],GetCurrentGameTime=held[2]},
            {__index=function(t,k) rawset(t,k,forbidden);return forbidden end})
        globals={Menus={}}
        for _,name in ipairs({'GetCellContent','OpenMenu','ConvertIDTo64Bit','IsValidWidgetElement',
            'IsButtonActive','IsValidComponent','GetComponentData','ConvertStringToLuaID','SetOrderParam',
            'GetOrderParams','IsDockingPossible','GetPlayerMoney','GetOwnLicences','HasLicence','ConvertStringTo64Bit',
            'GetActiveGuidanceMissionComponent','GetAutoPilotTarget','GetPlayerActivity','getElapsedTime'}) do
            globals[name]=forbidden
        end
        deps={ffi=ffi,C=C,globals=globals,Helper={convertComponentIDs=forbidden},
            native={RegisterEvent=function(name,fn) events[#events+1]=name end,
                GetNPCBlackboard=forbidden,SetNPCBlackboard=forbidden,AddUITriggeredEvent=forbidden,
                ConvertStringTo64Bit=forbidden,getElapsedTime=forbidden},
            resolve_issued=forbidden,clock=forbidden,read=forbidden,read_verbs={probe=true}}
        deps.Helper.addDelayedOneTimeCallbackOnUpdate=forbidden
    """)
    r.execute(payload.decode())
    r.execute("""
        X4ToolkitAutomationBundle.register_extension{id='installed-test',revision=string.rep('a',64),
            providers={['installed-test-v1']=string.rep('b',64)},attach=function(ctx)
                ctx.create_md_channel{screen='Test',event='Test.Replies',queue='$test_replies',controls={request=true}}
                local function close() extension_closed=(extension_closed or 0)+1;return true end
                ctx.on_dispose(close)
                ctx.registry.register{id='installed-test-v1',revision=string.rep('b',64),
                    actions={observe=true},session=function() return nil end,
                    invoke=function() error('not requested') end,dispose=close}
                return {dispose=close}
            end}
    """)
    r.execute('instance=X4ToolkitAutomationBundle.new(deps)')
    assert r.eval('native_calls')==0
    assert set(r.globals().events.values())=={'X4ToolkitSession.Replies','X4ToolkitSession.Invalidated'}
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    assert r.eval('native_calls')==0
    assert r.globals().extension_closed==1


def extension_runtime():
    payload,_=bundle.build(synthetic(),REV)
    r=runtime(payload)
    r.execute("""
        function descriptor(id,provider)
            return {id=id or 'test-extension',revision=string.rep('b',64),
                providers={[provider or 'test-provider']=string.rep('c',64)},
                attach=function(ctx)
                    attach_count=(attach_count or 0)+1;last_context=ctx
                    local function close() cleanup_count=(cleanup_count or 0)+1;return true end
                    ctx.on_dispose(close)
                    local spec={id=provider or 'test-provider',revision=string.rep('c',64),
                        actions={test=true},session=ctx.public_current,
                        invoke=function(phase,request,done)
                            invoke_count=(invoke_count or 0)+1;late_done=done
                        end,dispose=close}
                    last_spec=spec;ctx.registry.register(spec)
                    return {dispose=close}
                end}
        end
    """)
    return r


def test_catalog_copies_metadata_callback_and_provider_actions():
    r=extension_runtime()
    r.execute("""
        d=descriptor();X4ToolkitAutomationBundle.register_extension(d)
        d.id='changed';d.revision='wrong';d.providers['test-provider']='wrong'
        d.providers.evil=string.rep('d',64);d.attach=function() error('mutated') end
        X4ToolkitAutomationBundle.new(deps());last_spec.actions.evil=true
    """)
    assert r.globals().captured.provider_revisions['test-provider']=='c'*64
    assert r.globals().captured.provider_revisions.evil is None
    assert r.globals().last_context.extension_id=='test-extension'
    assert r.globals().last_context.extension_revision=='b'*64
    assert r.globals().all_registered['test-provider'].actions.evil is None
    assert set(r.globals().last_context.registry.keys())=={'register'}


@pytest.mark.parametrize('mutation',[
    "d.extra=true", "d.id=''", "d.revision=string.rep('B',64)", "d.attach=true",
    "d.providers={}", "d.providers={['x4-native-v1']=string.rep('a',64)}",
    "d.providers.bad='short'", "setmetatable(d,{})", "setmetatable(d.providers,{})",
    "for i=1,17 do d.providers['p'..i]=string.rep('a',64) end",
])
def test_catalog_rejects_malformed_descriptor(mutation):
    r=extension_runtime();r.execute('d=descriptor();'+mutation)
    assert r.eval('pcall(X4ToolkitAutomationBundle.register_extension,d)')[0] is False


@pytest.mark.parametrize('other',["descriptor()", "descriptor('other','test-provider')"])
def test_catalog_rejects_id_and_provider_collisions(other):
    r=extension_runtime();r.execute('X4ToolkitAutomationBundle.register_extension(descriptor())')
    assert r.eval('pcall(X4ToolkitAutomationBundle.register_extension,'+other+')')[0] is False


def test_catalog_sixteen_descriptor_bound_and_freeze_on_failed_first_new():
    r=extension_runtime()
    r.execute("for i=1,16 do X4ToolkitAutomationBundle.register_extension(descriptor('e'..i,'p'..i)) end")
    assert r.eval("pcall(X4ToolkitAutomationBundle.register_extension,descriptor('e17','p17'))")[0] is False
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,nil)')[0] is False
    assert r.eval("pcall(X4ToolkitAutomationBundle.register_extension,descriptor('e18','p18'))")[0] is False


def test_nine_distinct_descriptors_attach_without_catalog_exhaustion():
    r=extension_runtime()
    r.execute("for i=1,9 do X4ToolkitAutomationBundle.register_extension(descriptor('e'..i,'p'..i)) end")
    r.execute('X4ToolkitAutomationBundle.new(deps())')
    for i in range(1,10):
        assert r.globals().captured.provider_revisions['p'+str(i)]=='c'*64
        assert r.globals().all_registered['p'+str(i)] is not None


def test_catalog_rejects_even_matching_caller_allow_map():
    r=extension_runtime()
    r.execute("d=deps();d.provider_revisions={['x4-native-v1']=X4ToolkitAutomationBundle.build}")
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,d)')[0] is False
    assert r.globals().captured is None


@pytest.mark.parametrize('body',[
    "error('attach failed')", "return nil", "return {dispose=true}",
    "return {dispose=close}",
    "ctx.registry.register({id='wrong',revision=string.rep('c',64),actions={},session=function() end,invoke=function() end,dispose=close})",
    "ctx.registry.register({actions={},session=function() end,invoke=function() end,dispose=close})",
])
def test_partial_attach_always_disposes_acquired_resources_and_retires_context(body):
    r=extension_runtime()
    r.execute("""
        d=descriptor();d.attach=function(ctx)
            last_context=ctx
            local function close() cleanup_count=(cleanup_count or 0)+1;return true end
            ctx.on_dispose(close)
    """+body+"\nend;X4ToolkitAutomationBundle.register_extension(d)")
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().cleanup_count==1
    assert r.execute('return last_context.public_current()') is None
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().native_disposed==1


def test_each_provider_exactly_once_and_late_registration_sealed():
    r=extension_runtime()
    r.execute('X4ToolkitAutomationBundle.register_extension(descriptor());X4ToolkitAutomationBundle.new(deps())')
    assert r.eval('pcall(last_context.registry.register,last_spec)')[0] is False
    assert r.eval('pcall(last_context.on_dispose,function() return true end)')[0] is False
    r=extension_runtime()
    r.execute("d=descriptor();local original=d.attach;d.attach=function(ctx) local h=original(ctx);ctx.registry.register(last_spec);return h end;X4ToolkitAutomationBundle.register_extension(d)")
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().cleanup_count==1


def test_retirement_invalidates_callbacks_and_fresh_bootstrap_attaches_again():
    r=extension_runtime()
    r.execute("""
        X4ToolkitAutomationBundle.register_extension(descriptor());X4ToolkitAutomationBundle.new(deps())
        old=all_registered['test-provider'];old_context=last_context
        old.invoke('execute',{},function() delivered=true end)
        X4ToolkitAutomationBundle.new(deps());late_done()
    """)
    assert r.globals().attach_count==2 and r.globals().cleanup_count==1
    assert r.globals().delivered is None
    assert r.execute('return old.session()') is None
    assert r.execute('return old_context.public_current()') is None
    assert r.eval("pcall(old.invoke,'execute',{},function() end)")[0] is False
    assert r.globals().invoke_count==1
    assert r.globals().X4ToolkitAutomationBundle.dispose() is True
    assert r.globals().cleanup_count==2


@pytest.mark.parametrize('result',["return false", "error('failed disposer')", "return nil"])
def test_failed_extension_disposal_is_sticky_and_still_disposes_native(result):
    r=extension_runtime()
    r.execute("""
        d=descriptor();local attach=d.attach;d.attach=function(ctx)
            ctx.on_dispose(function() bad_count=(bad_count or 0)+1;"""+result+""" end)
            return attach(ctx)
        end
        X4ToolkitAutomationBundle.register_extension(d);X4ToolkitAutomationBundle.new(deps())
    """)
    assert r.eval('pcall(X4ToolkitAutomationBundle.dispose)')[0] is False
    assert r.globals().native_disposed==1 and r.globals().cleanup_count==1 and r.globals().bad_count==1
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False


def test_later_extension_failure_cleans_previous_extension_and_partial_current():
    r=extension_runtime()
    r.execute("""
        X4ToolkitAutomationBundle.register_extension(descriptor())
        local d=descriptor('second','second-provider')
        d.attach=function(ctx)
            ctx.on_dispose(function() second_closed=true;return true end)
            error('second failed')
        end
        X4ToolkitAutomationBundle.register_extension(d)
    """)
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().cleanup_count==1 and r.globals().second_closed is True
    assert r.globals().native_disposed==1
    assert r.execute("return all_registered['test-provider'].session()") is None


def test_cleanup_registration_bound_keeps_prior_resources_recoverable():
    r=extension_runtime()
    r.execute("""
        d=descriptor();d.attach=function(ctx)
            for i=1,17 do ctx.on_dispose(function() hook_count=(hook_count or 0)+1;return true end) end
        end
        X4ToolkitAutomationBundle.register_extension(d)
    """)
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')[0] is False
    assert r.globals().hook_count==16


@pytest.mark.parametrize('operation',["X4ToolkitAutomationBundle.new(deps())", "X4ToolkitAutomationBundle.dispose()"])
def test_attach_cannot_reenter_bundle_ownership(operation):
    r=extension_runtime()
    r.execute("d=descriptor();local attach=d.attach;d.attach=function(ctx) local h=attach(ctx);"+operation+
              ";return h end;X4ToolkitAutomationBundle.register_extension(d)")
    okay,reason=r.eval('pcall(X4ToolkitAutomationBundle.new,deps())')
    assert not okay and 'construction in progress' in reason
    assert r.globals().cleanup_count==1 and r.globals().native_disposed==1


def test_installed_channel_uses_scoped_dependencies_and_is_owned_on_attach_failure():
    r=extension_runtime()
    r.execute("""
        d=descriptor();d.attach=function(ctx)
            last_context=ctx
            channel=ctx.create_md_channel{screen='Fixed',event='Fixed.Replies',queue='$fixed',controls={request=true}}
            error('after channel acquisition')
        end
        X4ToolkitAutomationBundle.register_extension(d)
        supplied=deps();supplied.globals={ConvertIDTo64Bit=function() end}
        supplied.native={RegisterEvent=function() end};supplied.Helper={}
    """)
    assert r.eval('pcall(X4ToolkitAutomationBundle.new,supplied)')[0] is False
    assert r.globals().channel_closed==1
    assert r.eval('channel_deps.globals.RegisterEvent==supplied.native.RegisterEvent') is True
    assert r.eval('channel_deps.globals.ConvertIDTo64Bit==supplied.globals.ConvertIDTo64Bit') is True
    assert r.eval('channel_deps.public_current==last_context.public_current') is True
    assert r.execute('return channel_deps.public_current()') is None


def test_channel_creation_is_closed_after_attachment():
    r=extension_runtime()
    r.execute('X4ToolkitAutomationBundle.register_extension(descriptor());X4ToolkitAutomationBundle.new(deps())')
    assert r.eval('pcall(last_context.create_md_channel,{})')[0] is False
    assert r.globals().channel_deps is None


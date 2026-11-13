"""Actual main dispatcher/router/wire with synthetic bootstrap/native lifetime."""
import json
from pathlib import Path
import pytest
from test_modlua_rearm import _build,init,deliver,g
from x4validate._livepipe import decode_reply

UI=Path(__file__).resolve().parents[3]/"mods/x4_toolkit_helper/ui"
SESSION="a"*32;GENERATION="b"*32;REVISION="c"*64;WORLD="d"*32;BUILD="e"*64


def startup_runtime(in_menu=True, result="return {status='SCHEDULED',world_session=false}"):
    return _build(pre="""
        start_calls=0;required={};local original=require
        require=function(path) required[#required+1]=path;return original(path) end
        fake_C={IsStartmenu=function() return """+str(in_menu).lower()+""" end}
        local f=original('ffi');local define=f.cdef;local declared=false
        f.cdef=function(source)
            if source:find('bool IsStartmenu(void);',1,true) then declared=true end
            return define(source)
        end
        local query=fake_C.IsStartmenu
        fake_C.IsStartmenu=function() assert(declared,'undeclared symbol');return query() end
        X4ToolkitAutomationBundle={build=string.rep('e',64),start_ui=function(deps)
            start_calls=start_calls+1;start_deps=deps
            """+result+"""
        end}
    """)


def test_main_starts_ui_pipe_before_world_init_without_legacy_polling():
    r=startup_runtime()
    assert g(r,"start_calls")==1
    paths=list(g(r,"required").values())
    assert paths.index('extensions.sn_mod_support_apis.ui.named_pipes.Pipes') < paths.index(
        'extensions.sn_mod_support_apis.ui.named_pipes.Interface')
    assert len(g(r,"sched_reads"))==0 and len(g(r,"writes"))==0
    assert r.eval('start_deps.globals==_G and start_deps.Helper==Helper') is True
    assert any('X4TOOLKIT_UI_START OBSERVED SCHEDULED' in x for x in g(r,"log").values())


def test_main_skips_ui_pipe_outside_start_menu():
    r=startup_runtime(False)
    assert g(r,"start_calls")==0
    assert 'extensions.sn_mod_support_apis.ui.named_pipes.Pipes' not in list(g(r,"required").values())


@pytest.mark.parametrize('result',["error('startup failed')","return nil","return {status=4}"])
def test_main_ui_failure_is_reported_once_without_pre_world_retry(result):
    r=startup_runtime(result=result)
    assert g(r,"start_calls")==1 and len(g(r,"deferred"))==0
    assert any('X4TOOLKIT_UI_START UNAVAILABLE' in x for x in g(r,"log").values())
    assert g(r,"captured_init") is not None


def runtime(extra=""):
    r=_build(extra=extra)
    for key,file in (("wireFactory","live_actionwire.lua"),("coreFactory","live_actions.lua"),
                     ("routerFactory","live_automation.lua")):
        r.globals()[key]=r.execute((UI/file).read_text(encoding="utf-8"))
    r.execute("""
      constructors=0;disposals=0;ticks=0;pending_reply=nil
      stamp={provider='x4-live-session-v1',revision=string.rep('c',64),
             session=string.rep('a',32),generation=string.rep('b',32),world_token=string.rep('d',32)}
      codec=wireFactory()
      X4ToolkitAutomationBundle={new=function(deps)
        constructors=constructors+1
        local session={current=function() return stamp end,
          connect=function(req,done) pending_reply=done end,
          tick=function() ticks=ticks+1 end}
        local api={session=session,encode=codec.encode,
          dispose=function() disposals=disposals+1;return not disposal_fails end,
          ui_state=function(challenge)
            return {kind='UI_STATE',challenge=challenge,generation=stamp.generation,build=string.rep('e',64)}
          end}
        api.router=routerFactory({session=session,wire=codec,registry=coreFactory(),
          read=deps.read,read_verbs=deps.read_verbs,age=function() return 17 end,build=string.rep('e',64),
          ui_authorize=function(proof,gen) return proof==string.rep('f',32) and gen==stamp.generation end})
        return api
      end}
    """)
    init(r)
    return r


def ask(r,verb,*args,seq=1):
    before=len(g(r,"writes"))
    deliver(r,"\t".join(["MQ","1",str(seq),verb,*args]))
    assert len(g(r,"writes"))==before+1
    return decode_reply(g(r,"writes")[before+1]["msg"],seq)


def test_ui_state_exposes_loaded_bundle_not_legacy_only_build():
    r=runtime();reply=ask(r,"ui_state","f"*32)
    assert reply.status=="OK" and json.loads(reply.payload)["build"]==BUILD
    assert g(r,"constructors")==1


def test_actual_legacy_ping_through_bound_route_is_one_enveloped_reply():
    r=runtime()
    reply=ask(r,"bound",SESSION,GENERATION,REVISION,WORLD,BUILD,"ping")
    value=json.loads(reply.payload)
    assert reply.status=="OK" and value["status"]=="OK"
    assert "pong" in value["payload"] and value["stamp"]["session"]==SESSION


@pytest.mark.parametrize("target",["pause","unpause","ffisyms","echo","globals","bound"])
def test_bound_route_cannot_reach_legacy_writes_or_unlisted_reads(target):
    r=runtime()
    assert ask(r,"bound",SESSION,GENERATION,REVISION,WORLD,BUILD,target).status=="ERR"


@pytest.mark.parametrize("extra",[
    'verbs.ping=function(seq) reply(seq,"OK","first");reply(seq,"OK","second") end',
    'verbs.ping=function(seq) reply(seq,"OK","first");error("after read reply") end',
    'verbs.ping=function(seq) end',
])
def test_malformed_legacy_read_cannot_leak_an_extra_outer_frame(extra):
    r=runtime(extra)
    assert ask(r,"bound",SESSION,GENERATION,REVISION,WORLD,BUILD,"ping").status=="ERR"
    assert len(g(r,"writes"))==1
    # The capture slot is restored even after failure; regular ping can still reply.
    reply=ask(r,"ui_state","f"*32,seq=2)
    assert reply.status=="OK"


def test_session_handshake_schedules_timeout_before_async_reply():
    r=runtime()
    deliver(r,"\t".join(["MQ","1","1","session_connect","f"*32,"probe","","c"*32,GENERATION]))
    assert len(g(r,"writes"))==0 and len(g(r,"deferred"))==1
    g(r,"pending_reply")(r.table_from(dict(kind="UNAVAILABLE",reason="synthetic")))
    assert len(g(r,"writes"))==1
    assert decode_reply(g(r,"writes")[1]["msg"],1).status=="OK"
    g(r,"deferred")[1]["cb"]()
    assert g(r,"ticks")==1


def test_invalidation_disposes_then_recreates_on_next_ui_probe():
    r=runtime();ask(r,"ui_state","f"*32)
    r.execute("for _,cb in ipairs(ui_events['X4ToolkitSession.Invalidated']) do cb() end")
    assert g(r,"disposals")==1
    ask(r,"ui_state","1"*32,seq=2)
    assert g(r,"constructors")==2


def test_failed_invalidation_disposal_stays_refused():
    r=runtime();ask(r,"ui_state","f"*32);r.execute("disposal_fails=true")
    r.execute("for _,cb in ipairs(ui_events['X4ToolkitSession.Invalidated']) do cb() end")
    r.execute("disposal_fails=false")
    assert ask(r,"ui_state","1"*32,seq=2).status=="ERR"
    assert g(r,"constructors")==1


def test_legacy_ping_works_without_bundle():
    r=_build();init(r)
    assert ask(r,"ping").status=="OK"
    assert ask(r,"ui_state","f"*32,seq=2).status=="ERR"


@pytest.mark.parametrize("result",["nil","false","{}","{session={}}"])
def test_malformed_bundle_result_is_sticky_and_never_reconstructed(result):
    r=runtime()
    r.execute("X4ToolkitAutomationBundle.new=function() constructors=constructors+1;return "+result+" end")
    assert ask(r,"ui_state","f"*32).status=="ERR"
    assert ask(r,"ui_state","1"*32,seq=2).status=="ERR"
    assert g(r,"constructors")==1


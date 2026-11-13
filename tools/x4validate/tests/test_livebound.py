"""Actual routing Lua plus host codec/journal; synthetic native session and reads."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lupa.luajit21 import LuaRuntime
from x4validate._livebound import BoundPipe, session_exchange
from x4validate._livepipe import Reply
from x4validate._livesession import SessionUnavailable
from x4validate._liveactions import ActionJournal, ActionUncertain, ActionRefused
from x4validate._liveactionwire import PipeActionTransport, encode_request

ROOT=Path(__file__).resolve().parents[3]
SESSION="a"*32
GENERATION="b"*32
STAMP=dict(provider="x4-live-session-v1",revision="c"*64,session=SESSION,
           generation=GENERATION,world_token="d"*32)
IDENTITY=dict(provider=STAMP["provider"],revision=STAMP["revision"],session=SESSION,
              ui_generation=GENERATION,world_token=STAMP["world_token"],
              load_generation=1,world_lineage="UNKNOWN",peer={"pid":7})
REQUEST=dict(version=1,id="e"*32,session=SESSION,provider="fixture",action="start",
             arguments={"case":"one"},binding="prepared-token")


def runtime():
    r=LuaRuntime(unpack_returned_tuples=True)
    for name,file in (("wireFactory","live_actionwire"),("coreFactory","live_actions"),
                      ("routerFactory","live_automation")):
        r.globals()[name]=r.execute((ROOT/f"mods/x4_toolkit_helper/ui/{file}.lua").read_text())
    r.execute("""
    stamp={provider='x4-live-session-v1',revision=string.rep('c',64),
           session=string.rep('a',32),generation=string.rep('b',32),world_token=string.rep('d',32)}
    wire=wireFactory();core=coreFactory();calls={};nativeage=17
    core.register({id='fixture',revision=string.rep('f',64),actions={start=true},
      session=function() return stamp.session end,
      invoke=function(phase,req,done)
        calls[#calls+1]=phase
        done({status=phase=='prepare' and 'prepared' or phase=='execute' and 'running' or 'completed',
              reason='',observations={}})
      end})
    session={
      current=function()
        if missing then return nil,'missing' end
        return {provider=stamp.provider,revision=stamp.revision,session=stamp.session,
                generation=stamp.generation,world_token=stamp.world_token}
      end,
      connect=function(req,done) callback=done;connect_calls=(connect_calls or 0)+1 end}
    router=routerFactory({session=session,wire=wire,registry=core,build='12345678',
      ui_authorize=function(proof,generation) return proof=='ui-proof' and generation==stamp.generation end,
      age=function() if age_sequence then return table.remove(age_sequence,1) end;return nativeage end,read_verbs={probe=true},
      read=function(verb,args)
        read_calls=(read_calls or 0)+1
        read_args=args
        if drift then stamp.generation=string.rep('z',32) end
        return read_status or 'OK',read_payload or 'observed'
      end})
    """)
    return r


class Link:
    def identity(self):
        return copy.deepcopy(IDENTITY)
    def verify_reply(self,stamp):
        if stamp!=STAMP:
            raise SessionUnavailable("stamp mismatch")
        return self.identity()


class Pipe:
    def __init__(self,r):
        self.r=r;self.calls=[];self.seq=0;self.lose=False
    def ask(self,verb,*args):
        self.calls.append((verb,args));self.seq+=1
        assert verb=="bound"
        try:
            payload=self.r.globals().router.bound(self.r.table_from(args))
        except Exception as exc:
            return Reply(self.seq,"ERR",str(exc))
        if self.lose:
            self.lose=False
            raise TimeoutError("reply lost after dispatch")
        return Reply(self.seq,"OK",payload)


def bound(r):
    p=Pipe(r)
    return BoundPipe(p,Link(),"12345678"),p


def test_actual_lua_bound_read_and_host_identity():
    b,p=bound(runtime())
    reply=b.ask("probe")
    assert reply.payload=="observed" and reply.status=="OK"
    assert b.last_envelope["stamp"]==STAMP and b.last_envelope["age"]==17
    assert p.calls==[("bound",(SESSION,GENERATION,STAMP["revision"],STAMP["world_token"],"12345678","probe"))]


@pytest.mark.parametrize("verb",["pause","unpause","eval","bound","session_connect","unknown"])
def test_disallowed_read_never_calls_reader(verb):
    r=runtime();b,p=bound(r)
    with pytest.raises(SessionUnavailable): b.ask(verb)
    assert r.globals().read_calls is None


@pytest.mark.parametrize("mutation",[
    "missing=true","stamp.session=string.rep('f',32)","stamp.generation=string.rep('f',32)",
])
def test_stale_identity_refuses_before_dispatch(mutation):
    r=runtime();r.execute(mutation);b,p=bound(r)
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert r.globals().read_calls is None


def test_identity_change_during_read_poisoned():
    r=runtime();r.execute("drift=true");b,p=bound(r)
    with pytest.raises(SessionUnavailable): b.ask("probe")
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert len(p.calls)==1 and b.last_envelope is None


@pytest.mark.parametrize("value",["0/0","math.huge","-1","'17'"])
def test_invalid_native_age_refuses(value):
    r=runtime();r.execute("nativeage="+value);b,_=bound(r)
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert r.globals().read_calls is None


@pytest.mark.parametrize("mutation",[
    "stamp.revision=string.rep('f',64)","stamp.world_token=string.rep('f',32)",
])
def test_wrong_public_stamp_refuses_before_action(mutation):
    r=runtime();b,_=bound(r);t=PipeActionTransport(b);t.prepare(REQUEST)
    r.execute(mutation)
    with pytest.raises(SessionUnavailable): b.ask("action_execute",encode_request(REQUEST))
    assert len(r.globals().calls)==1


def test_wrong_build_refuses_before_read():
    r=runtime();p=Pipe(r);b=BoundPipe(p,Link(),"deadbeef")
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert r.globals().read_calls is None


@pytest.mark.parametrize("part",[3,4,5,7])
def test_changed_packet_under_same_digest_never_executes(part):
    r=runtime();b,_=bound(r);t=PipeActionTransport(b);t.prepare(REQUEST)
    fields=encode_request(REQUEST).split("|")
    fields[part]="other" if part!=7 else "case=s74776f"
    with pytest.raises(SessionUnavailable): b.ask("action_execute","|".join(fields))
    assert len(r.globals().calls)==1


def test_oversized_read_refuses_without_truncation():
    r=runtime();r.execute("read_payload=string.rep('x',4097)");b,_=bound(r)
    with pytest.raises(SessionUnavailable): b.ask("probe")


def test_game_age_rollback_refuses():
    r=runtime();b,_=bound(r);b.ask("probe");r.execute("nativeage=16")
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert r.globals().read_calls==1


@pytest.mark.parametrize("value",["16","0/0","math.huge","-1"])
def test_invalid_or_rolled_back_age_refuses_before_execute(value):
    r=runtime();b,_=bound(r);t=PipeActionTransport(b);t.prepare(REQUEST)
    r.execute("nativeage="+value)
    with pytest.raises(SessionUnavailable): b.ask("action_execute",encode_request(REQUEST))
    assert len(r.globals().calls)==1


def test_journal_lost_execute_reply_never_resends(tmp_path):
    r=runtime();b,p=bound(r);transport=PipeActionTransport(b)
    prepared=transport.prepare(REQUEST)
    journal=ActionJournal.create(tmp_path,REQUEST,prepared)
    p.lose=True
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    # New qualified transport may read status, but preserved journal cannot resend.
    b2,p2=bound(r);t2=PipeActionTransport(b2)
    with pytest.raises(ActionRefused):
        journal.execute(t2)
    assert not p2.calls
    assert journal.observe(t2)["status"]=="completed"
    assert [r.globals().calls[i] for i in range(1,len(r.globals().calls)+1)]==["prepare","execute","status"]


@pytest.mark.parametrize("field,value",[
    ("version",True),("version",2),("build","87654321"),("age",True),
    ("age",float("nan")),("age",-1),("status","PASS"),("payload",{}),
    ("stamp",dict(STAMP,generation="f"*32)),
])
def test_malformed_envelope_poisoned(field,value):
    envelope=dict(version=1,stamp=STAMP,build="12345678",age=17,status="OK",payload="")
    envelope[field]=value
    p=SimpleNamespace(ask=lambda *a:Reply(1,"OK",json.dumps(envelope)))
    b=BoundPipe(p,Link(),"12345678")
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert b.failed


def test_duplicate_json_fields_refuse():
    p=SimpleNamespace(ask=lambda *a:Reply(1,"OK",'{"version":1,"version":1}'))
    with pytest.raises(SessionUnavailable): BoundPipe(p,Link(),"12345678").ask("probe")


def test_session_exchange_exact_order_no_retry():
    calls=[]
    def ask(*args):
        calls.append(args)
        return Reply(1,"OK",'{"kind":"STATE"}')
    request=dict(mode="probe",session="",challenge="c"*32,generation=GENERATION)
    assert session_exchange(SimpleNamespace(ask=ask),request,ui_proof="d"*32)=={"kind":"STATE"}
    assert calls==[("session_connect","d"*32,"probe","","c"*32,GENERATION)]


def test_async_connect_duplicate_callback_sends_once():
    r=runtime()
    r.execute("""
      sends={}
      router.connect({'ui-proof','probe','',string.rep('e',32),string.rep('b',32)},
                     function(status,payload) sends[#sends+1]={status,payload} end)
      callback({kind='UNAVAILABLE',reason='test'})
      callback({kind='UNAVAILABLE',reason='duplicate'})
    """)
    assert len(r.globals().sends)==1


def test_pending_handshake_blocks_bound_dispatch():
    r=runtime()
    r.execute("router.connect({'ui-proof','probe','',string.rep('e',32),string.rep('b',32)},function() end)")
    b,_=bound(r)
    with pytest.raises(SessionUnavailable): b.ask("probe")
    assert r.globals().read_calls is None


def test_old_ui_ticket_never_reaches_session_handshake():
    r=runtime()
    ok=r.eval("pcall(function() router.connect({'retired','probe','',string.rep('e',32),string.rep('b',32)},function() end) end)")
    assert ok[0] is False and r.globals().connect_calls is None


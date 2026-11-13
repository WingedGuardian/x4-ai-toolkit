"""Actual Lua/Python codec controls with synthetic providers; no game calls."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lupa import LuaRuntime

from x4validate._liveactions import ActionRefused, ActionUncertain, request_digest
from x4validate._liveactionwire import encode_request, decode_receipt, PipeActionTransport
from x4validate._livepipe import encode_command, MAX_REQUEST_BYTES

SOURCE = Path(__file__).resolve().parents[3] / "mods/x4_toolkit_helper/ui/live_actionwire.lua"


def request(**args):
    return dict(version=1, id="a"*32, session="world.1", provider="native",
                action="view", binding="b"*64, arguments=args)


def codec():
    r = LuaRuntime(unpack_returned_tuples=True)
    return r, r.execute(SOURCE.read_text(encoding="utf-8"))()


@pytest.mark.parametrize("value", ["", "macro_a", "|,=\\\"", False, True, 0, -32, 1.25, 1e-8, 2**53-1])
def test_scalar_request_roundtrip_in_actual_lua(value):
    _, c = codec()
    q = request(argument=value)
    out = c.decode(encode_request(q))
    assert out["arguments"]["argument"] == value
    assert out["digest"] == request_digest(q)
    assert len(encode_command(99, "action_prepare", (encode_request(q),)).encode()) < MAX_REQUEST_BYTES


@pytest.mark.parametrize("value", [None, [], {}, float("nan"), float("inf"), 2**53, 10**400, "\t", "\n", "é"])
def test_unsupported_arguments_refused_before_transport(value):
    with pytest.raises(ActionRefused):
        encode_request(request(argument=value))


@pytest.mark.parametrize("mutation", [
    lambda p: p+"|extra",
    lambda p: p.replace("1|", "2|", 1),
    lambda p: p+"a=s61,a=s62",
    lambda p: p+"a=b2",
    lambda p: p+"a=s0a",
    lambda p: p+"a=sff",
    lambda p: p+"a=s0",
    lambda p: p+"a=nNaN",
    lambda p: p+"a=n1e999",
    lambda p: p+"a=n9007199254740992",
    lambda p: p+"a=n1+2",
    lambda p: p+"a.b=s61",
    lambda p: p+"a=s61,",
    lambda p: p+"x"*1500,
])
def test_actual_lua_refuses_malformed_packets(mutation):
    r, c = codec()
    ok, _ = r.eval("pcall")(c.decode, mutation(encode_request(request())))
    assert ok is False


def test_json_receipt_keeps_unicode_and_control_text():
    r, c = codec()
    value = r.table_from({"reason": 'line\n"quoted"\\é', "version": 1, "observations": r.table()})
    out = json.loads(c.encode(value))
    assert out == {"reason": 'line\n"quoted"\\é', "version": 1, "observations": {}}


@pytest.mark.parametrize("expression", [
    "{value=0/0}", "{value=math.huge}", "{value=9007199254740992}",
    "setmetatable({}, {})", "{[2]='sparse'}", "{[1]='array',key='mixed'}",
])
def test_json_refuses_non_json_values(expression):
    r,c = codec()
    ok,_ = r.eval("pcall")(c.encode, r.eval(expression))
    assert ok is False


def receipt(q, status="prepared"):
    return dict(version=1,id=q["id"],session=q["session"],digest=request_digest(q),
                status=status,observations={},reason="")


def test_async_prepare_not_misrepresented_as_prepared():
    q=request()
    raw=json.dumps(receipt(q,"preparing"))
    assert decode_receipt(raw,q,preparing=True)["status"] == "preparing"
    with pytest.raises(ActionUncertain):
        decode_receipt(raw,q)


@pytest.mark.parametrize("kind", ["id","session","digest","duplicate","nonfinite","huge"])
def test_python_refuses_bad_receipt(kind):
    q=request(); v=receipt(q)
    if kind in ("id","session","digest"):
        v[kind]="wrong"
    raw=json.dumps(v)
    if kind=="duplicate": raw=raw[:-1]+',"status":"completed"}'
    if kind=="nonfinite": raw=raw.replace('"observations": {}','"observations": {"bad": NaN}')
    if kind=="huge": raw=" "*65537
    with pytest.raises(ActionUncertain):
        decode_receipt(raw,q)


def test_no_transport_retry_on_lost_execution_reply():
    class Pipe:
        calls=0
        def ask(self, verb, payload):
            self.calls+=1
            assert verb=="action_execute"
            raise TimeoutError("reply lost")
    p=Pipe()
    with pytest.raises(TimeoutError):
        PipeActionTransport(p).execute(request())
    assert p.calls==1


def test_transport_returns_bound_readback_only():
    q=request()
    class Pipe:
        def ask(self, verb, payload):
            assert verb=="action_status"
            assert payload==encode_request(q)
            return SimpleNamespace(status="OK",payload=json.dumps(receipt(q,"running")))
    assert PipeActionTransport(Pipe()).status(q)["status"]=="running"


def test_non_ok_frame_cannot_be_completion():
    class Pipe:
        def ask(self,*_):
            return SimpleNamespace(status="ERR",payload="completed")
    with pytest.raises(ActionUncertain):
        PipeActionTransport(Pipe()).execute(request())


def test_hex_expansion_and_argument_count_are_bounded():
    with pytest.raises(ActionRefused,match="wire bound"):
        encode_request(request(text="x"*650))
    with pytest.raises(ActionRefused,match="24"):
        encode_request(request(**{f"k{i}":True for i in range(25)}))

def test_actual_lua_registry_wire_and_durable_journal_lost_reply(tmp_path):
    from x4validate._liveactions import ActionJournal
    r,c=codec()
    factory_path=SOURCE.with_name("live_actions.lua")
    r.globals().factory=r.execute(factory_path.read_text(encoding="utf-8"))
    r.execute("""
        registry=factory()
        mutations=0
        registry.register{id='native',revision=string.rep('b',64),actions={view=true},
            session=function() return 'world.1' end,
            invoke=function(phase,request,done)
                if phase=='prepare' then done{status='prepared',reason='',observations={}}
                elseif phase=='execute' then
                    mutations=mutations+1
                    done{status='running',reason='',observations={}}
                else done{status='completed',reason='',observations={synthetic=true}} end
            end}
    """)
    class Pipe:
        def ask(self, verb, payload):
            q=c.decode(payload)
            if verb=="action_prepare":
                result=r.globals().registry.prepare(q)
            elif verb=="action_execute":
                result=r.globals().registry.execute(q["id"],q["session"],q["digest"])
                raise TimeoutError("execution reply lost after synthetic mutation")
            else:
                assert verb=="action_status"
                result=r.globals().registry.status(q["id"],q["session"],q["digest"])
            return SimpleNamespace(status="OK",payload=c.encode(result))
    q=request(); transport=PipeActionTransport(Pipe())
    journal=ActionJournal.create(tmp_path,q,transport.prepare(q))
    with pytest.raises(ActionUncertain):
        journal.execute(transport)
    assert r.globals().mutations==1
    assert journal.observe(transport)["status"]=="completed"
    with pytest.raises(ActionRefused):
        journal.execute(transport)
    assert r.globals().mutations==1



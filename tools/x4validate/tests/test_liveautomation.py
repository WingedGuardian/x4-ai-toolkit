"""Durable preparation/CLI orchestration over synthetic trusted transport."""
import contextlib
import copy
import json
from types import SimpleNamespace
import pytest
from x4validate._liveactions import ActionRefused,ActionUncertain,request_digest
from x4validate._liveautomation import AutomationClient
from x4validate import _liveautomationcli as cli
from x4validate._livecli import cmd_query
from x4validate._livepipe import Reply
from x4validate._livesession import SessionUnavailable

REQUEST=dict(version=1,id="a"*32,session="b"*32,provider="fixture",action="start",
             arguments={},binding="token")


class Bound:
    def __init__(self):
        self.link=SimpleNamespace(identity=lambda:dict(session=REQUEST["session"]))
        self.last_envelope=None
        self.calls=[]
        self.result="prepared";self.lose=False
    def ask(self,verb,packet):
        self.calls.append(verb)
        if self.lose:
            self.lose=False
            raise TimeoutError("lost reply after invocation")
        return Reply(len(self.calls),"OK",json.dumps(dict(version=1,id=REQUEST["id"],
            session=REQUEST["session"],digest=request_digest(REQUEST),status=self.result,
            observations={},reason="")))


def client(tmp_path):
    b=Bound()
    return AutomationClient(b,tmp_path),b


def test_preparation_reserved_before_transport_and_never_replayed(tmp_path):
    c,b=client(tmp_path);c.prepare(REQUEST)
    assert json.loads((c.pending/(REQUEST["id"]+".json")).read_text(encoding="utf-8"))==REQUEST
    with pytest.raises(ActionRefused):c.prepare(REQUEST)
    assert b.calls==["action_prepare"]


def test_lost_preparation_reply_reconciles_without_repreparing(tmp_path):
    c,b=client(tmp_path);b.lose=True
    with pytest.raises(TimeoutError):c.prepare(REQUEST)
    assert c.status(REQUEST["id"])["status"]=="prepared"
    assert b.calls==["action_prepare","action_status"]


def test_async_preparation_then_read_only_readiness(tmp_path):
    c,b=client(tmp_path);b.result="preparing"
    assert c.prepare(REQUEST)["status"]=="preparing"
    assert not (c.actions/REQUEST["id"]).exists()
    b.result="prepared"
    assert c.status(REQUEST["id"])["status"]=="prepared"


def test_lost_execution_never_retried(tmp_path):
    c,b=client(tmp_path);c.prepare(REQUEST);b.lose=True
    with pytest.raises(ActionUncertain):c.execute(REQUEST["id"])
    with pytest.raises(ActionRefused):c.execute(REQUEST["id"])
    b.result="completed"
    assert c.status(REQUEST["id"])["status"]=="completed"
    assert b.calls==["action_prepare","action_execute","action_status"]


def test_new_world_refuses_existing_request_before_transport(tmp_path):
    c,b=client(tmp_path);c.prepare(REQUEST)
    b.link.identity=lambda:dict(session="c"*32)
    with pytest.raises(ActionRefused):c.execute(REQUEST["id"])
    assert b.calls==["action_prepare"]


@pytest.mark.parametrize("verb",sorted(cli.RESERVED_VERBS|{"pause","unpause"}))
def test_public_query_blocks_transport_before_open(verb,monkeypatch):
    def forbidden(*a,**k):raise AssertionError("pipe must not open")
    monkeypatch.setattr("x4validate._livecli._live_open",forbidden)
    assert cmd_query(verb,[],None,10)==2


BASE=["--journal","unused","--image","X4.exe","--profile-id","123",
      "--build","d"*64,"--revision","e"*64,"--mode","resume"]


@pytest.mark.parametrize("verb",sorted(cli.RESERVED_VERBS|{"pause","unpause"}))
def test_automation_query_blocks_transport_before_open(verb):
    def forbidden(*a,**k):raise AssertionError("pipe must not open")
    assert cli.main(BASE+["query",verb],open_pipe=forbidden)==2


def test_cli_injects_measured_session_and_announces_execute(tmp_path,capsys):
    spec={k:v for k,v in REQUEST.items() if k not in ("version","session")}
    path=tmp_path/"request.json";path.write_text(json.dumps(spec))
    prepared=[]
    c=SimpleNamespace(identity=lambda:dict(session="f"*32),
        prepare=lambda r:(prepared.append(r) or dict(status="prepared")),
        output=lambda **kw:kw)
    @contextlib.contextmanager
    def pipe(*args):yield object()
    code=cli.main(BASE+["prepare",str(path)],open_pipe=pipe,connect=lambda *a,**kw:c)
    assert code==0 and prepared[0]["session"]=="f"*32
    out=capsys.readouterr()
    assert "EXPERIMENTAL" in out.err and json.loads(out.out)["receipt"]["status"]=="prepared"


def test_cli_pending_is_not_a_completed_zero_exit(capsys,tmp_path):
    (tmp_path/"requests").mkdir()
    (tmp_path/"requests"/("a"*32+".json")).write_text(json.dumps(REQUEST))
    base=list(BASE);base[1]=str(tmp_path)
    c=SimpleNamespace(execute=lambda i:dict(status="running"),output=lambda **kw:kw)
    @contextlib.contextmanager
    def pipe(*args):yield object()
    assert cli.main(base+["execute","a"*32],open_pipe=pipe,connect=lambda *a,**kw:c)==4
    out=capsys.readouterr();assert "WRITE:" in out.err


def test_cli_bad_spec_refuses_before_open(tmp_path):
    path=tmp_path/"bad.json";path.write_text('{"session":"invented"}')
    def forbidden(*a,**kw):raise AssertionError("pipe must not open")
    assert cli.main(BASE+["prepare",str(path)],open_pipe=forbidden)==2


def test_post_dispatch_output_failure_preserves_receipt_and_uncertainty(capsys,tmp_path):
    (tmp_path/"requests").mkdir()
    (tmp_path/"requests"/("a"*32+".json")).write_text(json.dumps(REQUEST))
    base=list(BASE);base[1]=str(tmp_path)
    calls=[]
    def execute(i):
        calls.append(i);return dict(status="completed")
    def output(**kw):raise SessionUnavailable("peer exited after reply")
    c=SimpleNamespace(execute=execute,output=output)
    @contextlib.contextmanager
    def pipe(*args):yield object()
    assert cli.main(base+["execute","a"*32],open_pipe=pipe,connect=lambda *a,**kw:c)==3
    result=json.loads(capsys.readouterr().out)
    assert calls==["a"*32] and result["may_have_acted"] is True
    assert result["returned_receipt"]=={"status":"completed"} and result["status"]=="UNKNOWN"


"""Synthetic UI bootstrap/control flow; no native VM or peer qualification."""
import copy
import json
from types import SimpleNamespace
import pytest
from x4validate._liveuiidentity import UILink
from x4validate._livesession import SessionJournal, SessionUnavailable
from x4validate._livepipe import Reply

BUILD="b"*64
PEER=dict(pid=17,creation_filetime="123456789",image="C:/game/X4.exe")


class Bootstrap:
    def __init__(self):
        self.generation=""
        self.proof=None
        self.calls=[]
        self.mutate=None
    def ask(self,verb,*args):
        self.calls.append((verb,args))
        if verb=="ui_state":
            self.proof=args[0]
            value=dict(kind="UI_STATE",challenge=args[0],generation=self.generation,build=BUILD)
            if self.mutate: self.mutate(value)
            return Reply(1,"OK",json.dumps(value))
        assert verb=="session_connect"
        proof,mode,session,challenge,generation=args
        if proof!=self.proof or (self.generation and generation!=self.generation):
            return Reply(2,"ERR","stale UI proof")
        self.generation=generation
        return Reply(2,"OK",json.dumps(dict(mode=mode,challenge=challenge,generation=generation)))


def link(tmp_path):
    b=Bootstrap()
    return UILink(b,SessionJournal(tmp_path/"ui-generations"),lambda:copy.deepcopy(PEER),BUILD),b


def request(generation):
    return dict(mode="probe",session="",challenge="a"*32,generation=generation)


def test_new_ui_allocated_before_first_md_handshake(tmp_path):
    l,b=link(tmp_path);identity=l.probe()
    assert identity["sequence"]==1 and b.generation==""
    assert l.journal.records()[0]["session"]==identity["generation"]
    l.exchange(request(identity["generation"]))
    assert b.generation==identity["generation"]


def test_separate_clients_resume_only_durable_same_ui_generation(tmp_path):
    l,b=link(tmp_path);i=l.probe();l.exchange(request(i["generation"]))
    second=UILink(b,l.journal,lambda:PEER,BUILD)
    assert second.probe()["generation"]==i["generation"]
    assert len(second.journal.records())==1


def test_new_ui_always_gets_new_nonce_even_same_process(tmp_path):
    l,b=link(tmp_path);i=l.probe();l.exchange(request(i["generation"]))
    b.generation="";b.proof=None
    successor=UILink(b,l.journal,lambda:PEER,BUILD);j=successor.probe()
    assert j["generation"]!=i["generation"] and j["sequence"]==2


def test_reload_between_ui_probe_and_session_handshake_refuses(tmp_path):
    l,b=link(tmp_path);i=l.probe()
    b.proof=None
    with pytest.raises(SessionUnavailable): l.exchange(request(i["generation"]))
    assert b.generation=="" and l.generation is None


@pytest.mark.parametrize("field,value",[
    ("kind","READY"),("challenge","f"*32),("generation",True),
    ("generation","bad"),("build","c"*64),("extra",1),
])
def test_malformed_ui_response_cannot_allocate(tmp_path,field,value):
    l,b=link(tmp_path);b.mutate=lambda v:v.__setitem__(field,value)
    with pytest.raises(SessionUnavailable): l.probe()
    assert l.journal.records()==[] and l.generation is None


def test_foreign_bound_generation_is_not_adopted(tmp_path):
    l,b=link(tmp_path);b.generation="f"*32
    with pytest.raises(SessionUnavailable): l.probe()
    assert l.journal.records()==[]


def test_journal_change_after_probe_prevents_handshake(tmp_path):
    l,b=link(tmp_path);i=l.probe()
    rows=l.journal.records();l.journal.allocate(PEER,BUILD,rows)
    with pytest.raises(SessionUnavailable): l.exchange(request(i["generation"]))
    assert len(b.calls)==1


def test_peer_change_after_probe_prevents_handshake(tmp_path):
    l,b=link(tmp_path);i=l.probe();l.peer=lambda:dict(PEER,pid=18)
    with pytest.raises(SessionUnavailable): l.exchange(request(i["generation"]))
    assert len(b.calls)==1


def test_wrong_generation_never_sent(tmp_path):
    l,b=link(tmp_path);l.probe()
    with pytest.raises(SessionUnavailable): l.exchange(request("f"*32))
    assert len(b.calls)==1


def test_lost_ack_preserves_allocated_nonce_for_status_probe(tmp_path):
    l,b=link(tmp_path);i=l.probe();ask=b.ask
    def lost(*args):
        reply=ask(*args)
        if args[0]=="session_connect": raise TimeoutError("after native generation bound")
        return reply
    b.ask=lost
    with pytest.raises(SessionUnavailable): l.exchange(request(i["generation"]))
    b.ask=ask
    successor=UILink(b,l.journal,lambda:PEER,BUILD)
    assert successor.probe()["generation"]==i["generation"]
    assert len(successor.journal.records())==1


def test_ambiguous_unbound_attempt_never_reuses_reserved_nonce(tmp_path):
    l,b=link(tmp_path);i=l.probe() # no native binding yet; local process then dies
    successor=UILink(b,l.journal,lambda:PEER,BUILD);j=successor.probe()
    assert j["generation"]!=i["generation"] and j["sequence"]==2


def test_old_proof_superseded_by_other_probe_refuses(tmp_path):
    l,b=link(tmp_path);i=l.probe();b.ask("ui_state","e"*32)
    with pytest.raises(SessionUnavailable): l.exchange(request(i["generation"]))
    assert b.generation==""


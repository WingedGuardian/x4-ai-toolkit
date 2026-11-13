"""Actual router/host capture chain; synthetic legacy/native source data."""
import json
import pytest
from test_livebound import runtime,bound,SESSION
from x4validate._liveautomation import AutomationClient
from x4validate._livereadpages import ReadCapture
from x4validate._livesession import SessionUnavailable

CAPTURE="f"*32


def collected(text="small",status="OK"):
    r=runtime();r.globals().read_payload=text;r.globals().read_status=status
    b,p=bound(r)
    header=b.ask("read_capture",CAPTURE,"probe","subject","field")
    capture=ReadCapture(header.payload,capture=CAPTURE,verb="probe")
    pages=[b.ask("read_page",CAPTURE,str(i)).payload for i in range(capture.header["pages"])]
    return r,b,p,header.payload,pages


@pytest.mark.parametrize("size",[0,1,999,1000,1001,4096,30000,32000])
def test_full_capture_size_bound_and_one_source_read(tmp_path,size):
    r=runtime();r.globals().read_payload="x"*size;b,p=bound(r)
    c=AutomationClient(b,tmp_path);reply=c.query("probe","subject","field")
    assert reply.payload=="x"*size and c.last_read["bytes"]==size
    assert c.last_read["transport_complete"] and c.last_read["atomic"] is False
    assert r.globals().read_calls==1
    assert [r.globals().read_args[i] for i in (1,2)]==["subject","field"]
    assert len(p.calls)==1+max(1,(size+999)//1000)


def test_utf8_crossing_page_boundary_roundtrips_without_replacement(tmp_path):
    r=runtime();text="x"*999+"艦船"*400;r.globals().read_payload=text
    b,_=bound(r);c=AutomationClient(b,tmp_path)
    assert c.query("probe").payload==text


@pytest.mark.parametrize("status",["OK","ABSENT","ERR"])
def test_source_status_is_not_promoted_by_transport_completion(tmp_path,status):
    r=runtime();r.globals().read_status=status;b,_=bound(r);c=AutomationClient(b,tmp_path)
    assert c.query("probe").status==status
    assert c.last_read["source_status"]==status


def test_captured_bytes_immutable_when_underlying_read_changes():
    r=runtime();r.execute("read_payload=string.rep('x',2000)");b,_=bound(r)
    first=b.ask("read_capture",CAPTURE,"probe")
    c=ReadCapture(first.payload,capture=CAPTURE,verb="probe")
    r.execute("read_payload='new value'")
    for i in range(c.header["pages"]):c.add(b.ask("read_page",CAPTURE,str(i)).payload)
    assert c.finish()["payload"]=="x"*2000 and r.globals().read_calls==1


def test_oversized_source_refuses_instead_of_truncating(tmp_path):
    r=runtime();r.execute("read_payload=string.rep('x',32001)");b,_=bound(r)
    c=AutomationClient(b,tmp_path)
    with pytest.raises(SessionUnavailable):c.query("probe")
    assert c.last_read is None


def test_clock_rollback_between_source_finish_and_envelope_refuses(tmp_path):
    r=runtime();r.execute("age_sequence={10,20,15}");b,_=bound(r)
    c=AutomationClient(b,tmp_path)
    with pytest.raises(SessionUnavailable):c.query("probe")
    assert c.last_read is None and r.globals().read_calls==1


def test_capture_nonce_cannot_be_reused():
    r,b,p,header,pages=collected()
    with pytest.raises(SessionUnavailable):b.ask("read_capture",CAPTURE,"probe")
    assert r.globals().read_calls==1


def test_superseded_capture_cannot_be_read():
    r,b,p,header,pages=collected()
    b.ask("read_capture","e"*32,"probe")
    with pytest.raises(SessionUnavailable):b.ask("read_page",CAPTURE,"0")


def test_expired_capture_refuses():
    r,b,p,header,pages=collected()
    r.execute("nativeage=318")
    with pytest.raises(SessionUnavailable):b.ask("read_page",CAPTURE,"0")


@pytest.mark.parametrize("field,value",[
    ("capture","e"*32),("bytes",123),("pages",99),("index",1),
    ("offset",1),("length",0),("hex","gg"),("source_status","PASS"),
    ("started_age",float("nan")),("finished_age",10**500),("atomic",True),
])
def test_page_counterexamples_never_complete(field,value):
    _,_,_,header,pages=collected()
    c=ReadCapture(header,capture=CAPTURE,verb="probe")
    page=json.loads(pages[0]);page[field]=value
    with pytest.raises(SessionUnavailable):c.add(json.dumps(page))
    with pytest.raises(SessionUnavailable):c.finish()


def test_missing_and_duplicate_pages_refuse():
    _,_,_,header,pages=collected("x"*1001)
    c=ReadCapture(header,capture=CAPTURE,verb="probe");c.add(pages[0])
    with pytest.raises(SessionUnavailable):c.finish()
    c=ReadCapture(header,capture=CAPTURE,verb="probe");c.add(pages[0])
    with pytest.raises(SessionUnavailable):c.add(pages[0])


def test_wrong_requested_capture_or_verb_refuses():
    _,_,_,header,_=collected()
    with pytest.raises(SessionUnavailable):ReadCapture(header,capture="e"*32,verb="probe")
    with pytest.raises(SessionUnavailable):ReadCapture(header,capture=CAPTURE,verb="player")


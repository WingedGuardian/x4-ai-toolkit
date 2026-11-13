"""Verified exchange history controls; all transports are synthetic, never native."""
import importlib.util
from pathlib import Path
import pytest
from x4validate import _livesession as S
from test_livesession import setup, start, stamp, UI, REV

def test_unready_has_no_receipt(tmp_path):
    _, _, link = setup(tmp_path)
    with pytest.raises(S.SessionUnavailable):
        link.handshake_receipt()

def test_copied_verified_exchange_without_another_query(tmp_path):
    t, _, link = setup(tmp_path)
    identity = start(link)
    count = len(t.calls)
    row = link.handshake_receipt()
    assert row["request"] == t.calls[-1]
    assert row["identity"] == identity
    assert row["reply"]["kind"] == "READY"
    assert row["reply"]["age"] == 10
    assert row["native_age_domain"] == "MD_PLAYER_AGE"
    assert row["peer_before"] == row["peer_after"] == identity["peer"]
    assert row["world_lineage"] == "UNKNOWN"
    assert row["cryptographic_attestation"] is False
    assert row["scope"] == "VERIFIED_EXCHANGE_RECORD_NOT_FRESH_NATIVE_STATE"
    assert "evidence_tier" not in row
    row["reply"]["age"] = 999
    row["identity"]["peer"]["pid"] = 456
    assert link.handshake_receipt()["reply"]["age"] == 10
    assert link.handshake_receipt()["identity"] == identity
    assert len(t.calls) == count

def test_resume_records_new_exchange_not_new_load(tmp_path):
    t, journal, link = setup(tmp_path)
    first = start(link)
    old = link.handshake_receipt()
    other = S.SessionLink(journal, lambda: t.peer, t.exchange, REV, "c"*32)
    resumed = other.resume(other.probe())
    row = other.handshake_receipt()
    assert row["request"] == t.calls[-1]
    assert row["reply"]["mode"] == "resume"
    assert row["reply"]["challenge"] != old["reply"]["challenge"]
    assert resumed["load_generation"] == first["load_generation"]
    assert resumed["session"] == first["session"]
    assert row["reply"]["generation"] != old["reply"]["generation"]

@pytest.mark.parametrize("operation", ["probe", "foreign_fresh", "timeout", "malformed"])
def test_next_attempt_never_retains_old_receipt(tmp_path, operation):
    t, _, link = setup(tmp_path)
    start(link)
    if operation == "timeout":
        t.drop = True
    elif operation == "malformed":
        t.mutate = lambda row: dict(row, age=float("nan"))
    try:
        if operation == "foreign_fresh":
            link.fresh(object())
        else:
            link.probe()
    except S.SessionUnavailable:
        pass
    with pytest.raises(S.SessionUnavailable):
        link.handshake_receipt()

@pytest.mark.parametrize("drift", ["peer", "journal", "native_reply"])
def test_identity_refusals_invalidate_record(tmp_path, drift):
    t, journal, link = setup(tmp_path)
    identity = start(link)
    if drift == "peer":
        t.peer["pid"] += 1
    elif drift == "journal":
        journal.allocate(t.peer, REV, journal.records())
    else:
        with pytest.raises(S.SessionUnavailable):
            link.verify_reply(dict(stamp(identity), world_token="changed"))
    with pytest.raises(S.SessionUnavailable):
        link.handshake_receipt()

def test_record_is_historical_not_fresh_native_proof(tmp_path):
    t, _, link = setup(tmp_path)
    start(link)
    t.session = "d"*32  # native state changed without a new host exchange
    assert link.handshake_receipt()["reply"]["session"] != t.session
    assert link.handshake_receipt()["scope"].endswith("NOT_FRESH_NATIVE_STATE")

def test_actual_producer_grammar_matches_retained_fields(tmp_path):
    # Pure generation only. This checks the actual emitted contract, not MD execution.
    path = Path(__file__).resolve().parents[3] / "scripts/live_session_md.py"
    spec = importlib.util.spec_from_file_location("handshake_source", path)
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    outputs, receipt = producer.prepare()
    from lxml import etree
    root = etree.fromstring(outputs["md/X4ToolkitSession.xml"])
    marker = root.xpath(".//debug_text[contains(@text, 'X4TKSESSION HANDSHAKE')]")
    assert len(marker) == 1
    template, arguments = marker[0].get("text").split("'.[")
    assert arguments == "$R.$mode,$Kind,$Session,$R.$generation,$R.$challenge,player.age]"
    t, _, link = setup(tmp_path)
    start(link)
    row = link.handshake_receipt()["reply"]
    line = template[1:] % tuple(row[k] for k in ("mode","kind","session","generation","challenge","age"))
    assert f"session={row['session']}" in line
    assert f"challenge={row['challenge']}" in line
    assert line.endswith("age=10")
    assert "world_token" not in line and "build=" not in line
    assert set(row) == set(receipt["contract"]["fields"])

@pytest.mark.parametrize("drift", [False, True])
def test_actual_sessionlink_to_raw_paged_read_identity(tmp_path, drift):
    from test_livebound import runtime, Pipe
    from test_liveevidence import Peer
    from x4validate._livebound import BoundPipe
    from x4validate._liveautomation import AutomationClient
    from x4validate._liveevidence import ReadEvidence, replay
    t, journal, initial = setup(tmp_path / "session")
    peer = Peer()
    peer.value = dict(t.peer)
    link = S.SessionLink(journal, peer, t.exchange, REV, UI)
    identity = start(link)
    recorded = link.handshake_receipt()
    r = runtime()
    r.globals().stamp = r.table_from(stamp(identity))
    r.globals().read_payload = "x" * 1001
    bound = BoundPipe(Pipe(r), link, "12345678")
    (tmp_path / "actions").mkdir()
    collector = ReadEvidence(AutomationClient(bound, tmp_path / "actions"), synthetic=True)
    if drift:
        r.globals().stamp["session"] = "e" * 32
        with pytest.raises(S.SessionUnavailable):
            collector.collect("probe")
        assert not collector.handles
    else:
        handle = collector.collect("probe")
        row = collector.receipt(handle)
        parsed = replay(collector.transcript(handle))
        assert row["identity"] == recorded["identity"] == parsed["identity"]
        assert row["evidence_tier"] == "SYNTHETIC"
        assert parsed["provenance"] == "EXPORTED_REPLAY_NOT_AUTHENTICATED"
        assert row["world_lineage"] == "UNKNOWN"
        assert len(collector.transcript(handle)["frames"]) == 3
        assert link.handshake_receipt() == recorded

"""Damaged indexes are non-answers, never certified absence."""
import hashlib
import json
from pathlib import Path
import pytest
from x4validate import _xref, _freshness, _registry, _merge


@pytest.fixture(autouse=True)
def isolated_roots(monkeypatch):
    monkeypatch.setattr(_registry, 'default_installed_dirs', lambda: [])
    monkeypatch.setattr(_merge.Config, 'dlc_dirs', lambda self: [])


HEADER = 'kind\tname\tsource\tfile\tcue\tline\ttarget\n'
ROW = 'action\tfind_station\tbase\tmd/a.xml\tC\t3\t\n'


@pytest.mark.parametrize('body', [
    HEADER+ROW.rstrip()+'\n', HEADER+ROW.replace('\t3\t', '\tbad\t'),
    HEADER+ROW.replace('\t3\t', '\t\t'), HEADER+ROW.replace('\t3\t', '\t-1\t'),
    HEADER+ROW.replace('action\t', 'unknown\t'), HEADER+ROW.replace('base\t', '\t'),
    HEADER.replace('kind', 'wrong')+ROW, '', HEADER+'\n',
])
def test_bad_tsv_refused_without_silent_loss(tmp_path, capsys, body):
    path = tmp_path/'xref.tsv'; path.write_text(body, encoding='utf-8')
    assert _xref.main(['who-calls', 'absent', '--tsv', str(path)]) == 2
    cap = capsys.readouterr()
    assert 'real negative' not in cap.out
    assert str(path) in cap.err


def test_invalid_utf8_refused(tmp_path):
    path = tmp_path/'xref.tsv'; path.write_bytes(HEADER.encode()+b'\xff')
    assert _xref.main(['cue', 'absent', '--tsv', str(path)]) == 2


def built(tmp_path):
    ref = tmp_path/'ref'; (ref/'md').mkdir(parents=True)
    (ref/'md/a.xml').write_text('<mdscript name="S"><cues><cue name="C"><actions><find_station/></actions></cue></cues></mdscript>')
    ext = tmp_path/'ext'; ext.mkdir()
    path = tmp_path/'xref.tsv'
    assert _xref.main(['build', '--reference', str(ref), '--ext-dir', str(ext), '--out', str(path)]) == 0
    return path, ref


def test_new_build_signed_and_absence_certified(tmp_path, capsys):
    path, _ = built(tmp_path)
    stamp = _freshness.read_sidecar(path)
    assert stamp.get('artifact_sha256') == hashlib.sha256(path.read_bytes()).hexdigest()
    capsys.readouterr()
    assert _xref.main(['who-calls', 'absent', '--tsv', str(path)]) == 0
    assert 'real negative' in capsys.readouterr().out


@pytest.mark.parametrize('mutation', ['delete-row', 'edit-row', 'exclusions'])
def test_signed_artifact_tampering_refuses_all_queries(tmp_path, mutation):
    path, _ = built(tmp_path)
    if mutation == 'delete-row':
        path.write_text(HEADER)
    elif mutation == 'edit-row':
        path.write_text(path.read_text().replace('find_station', 'different'))
    else:
        _xref._sidecar(path).write_text('new-exclusion.xml\n')
    assert _xref.main(['who-calls', 'find_station', '--tsv', str(path)]) == 2


def test_unsigned_legacy_positive_warned_absence_refused(tmp_path, capsys):
    path, _ = built(tmp_path)
    stamp = _freshness.read_sidecar(path); stamp.pop('artifact_sha256', None)
    _freshness.stamp_sidecar(path, stamp)
    capsys.readouterr()
    assert _xref.main(['who-calls', 'find_station', '--tsv', str(path)]) == 0
    assert 'integrity' in capsys.readouterr().err.lower()
    assert _xref.main(['who-calls', 'absent', '--tsv', str(path)]) == 2
    assert 'real negative' not in capsys.readouterr().out


def test_stale_signed_index_cannot_certify_absence(tmp_path, capsys):
    path, ref = built(tmp_path)
    (ref/'md/a.xml').write_text('<mdscript name="changed"/>')
    capsys.readouterr()
    assert _xref.main(['cue', 'absent', '--tsv', str(path)]) == 2
    assert 'real negative' not in capsys.readouterr().out


def test_valid_header_only_and_bom_are_structurally_valid(tmp_path):
    path = tmp_path/'xref.tsv'; path.write_bytes(b'\xef\xbb\xbf'+HEADER.encode())
    assert _xref.read_tsv(path) == []


@pytest.mark.parametrize('field,value', [
    ('reference_path', ['unexpected-list']), ('reference_path', 123),
    ('content', []), ('engine', {}), ('detail', ['invalid']), ('detail', [{}]),
    ('roots', 'not-a-list'), ('roots', [1]),
])
def test_malformed_freshness_refused_without_traceback(tmp_path, capsys, field, value):
    path, _ = built(tmp_path)
    stamp = _freshness.read_sidecar(path); stamp[field] = value
    if field == 'detail' and value == [{}]:
        stamp['content'] = 'changed'; stamp['reference'] = 'changed'
    _freshness.stamp_sidecar(path, stamp)
    capsys.readouterr()
    assert _xref.main(['who-calls', 'find_station', '--tsv', str(path)]) == 2
    err = capsys.readouterr().err
    assert 'rebuild' in err.lower() and 'Traceback' not in err


def test_check_index_is_the_query_paths_verdict(tmp_path):
    """x4fresh and the query path share check_index: a fresh signed build reads fresh and signed,
    a tampered one raises one of INDEX_UNREADABLE (never a traceback type outside it)."""
    path, ref = built(tmp_path)
    rows, signed, excl, verdict = _xref.check_index(path)
    assert rows and len(rows) == len(_xref.read_tsv(path))
    assert (signed, excl, verdict.fresh) == (True, True, True)
    (ref/'md/a.xml').write_text('<mdscript name="changed"/>')
    assert _xref.check_index(path)[3].fresh is False
    path.write_text(HEADER)
    with pytest.raises(_xref.INDEX_UNREADABLE):
        _xref.check_index(path)


def test_an_INCOMPLETE_index_negative_is_DEGRADED_rc_3_never_0(tmp_path, capsys, monkeypatch):
    """v4.0.0 review R5-11: the coverage note said 'INCOMPLETE ... any negative here is a lead,
    not a finding' while the verdict said 'a real negative' with exit 0. An index that misses
    installed DLC cannot certify an absence: exit 3 (degraded), never 0, never 'real'."""
    path, _ = built(tmp_path)
    capsys.readouterr()
    monkeypatch.setattr(_xref, '_expected_dlc', lambda: 1)
    rc = _xref.main(['who-calls', 'absent', '--tsv', str(path)])
    cap = capsys.readouterr()
    assert 'INCOMPLETE' in cap.out, cap.out
    assert rc == 3, (rc, cap.out, cap.err)
    assert 'real negative' not in cap.out


def test_TWIN_a_COMPLETE_index_negative_stays_certified(tmp_path, capsys, monkeypatch):
    path, _ = built(tmp_path)
    capsys.readouterr()
    monkeypatch.setattr(_xref, '_expected_dlc', lambda: 0)
    assert _xref.main(['who-calls', 'absent', '--tsv', str(path)]) == 0
    assert 'real negative' in capsys.readouterr().out

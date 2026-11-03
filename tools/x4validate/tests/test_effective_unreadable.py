"""Store failures are actionable rc 2; user SQL mistakes remain rc 1."""
import sqlite3
import pytest
from x4validate import _closedpipe, _effective, _effectivecli, _freshness


def store(path):
    with sqlite3.connect(path) as con:
        con.executescript(_effective._SCHEMA)
        con.execute('INSERT INTO meta VALUES (?,?)', ('schema_version', str(_effective.SCHEMA_VERSION)))
        con.execute("INSERT INTO entities VALUES (1, 'ware', 'ore', 'solid', 'libraries/wares.xml', 'base', NULL)")
    return path


@pytest.mark.parametrize('failure', ['missing', 'text', 'truncated', 'empty-schema', 'version', 'missing-column'])
@pytest.mark.parametrize('command', [('ls', 'ware'), ('sql', 'SELECT 1')])
def test_bad_store_is_rc2(tmp_path, capsys, failure, command):
    path = tmp_path/'bad.sqlite'
    if failure == 'text':
        path.write_bytes(b'not sqlite')
    elif failure == 'truncated':
        store(path); path.write_bytes(path.read_bytes()[:101])
    elif failure == 'empty-schema':
        sqlite3.connect(path).close()
    elif failure == 'version':
        store(path)
        with sqlite3.connect(path) as con:
            con.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    elif failure == 'missing-column':
        store(path)
        with sqlite3.connect(path) as con:
            con.execute('DROP INDEX idx_ent_kind_klass')
            con.execute('ALTER TABLE entities DROP COLUMN klass')
    assert _effectivecli.main(['--db', str(path), *command]) == 2
    err = capsys.readouterr().err
    assert str(path) in err and 'build' in err


def test_valid_store_readonly_and_sql_errors_preserved(tmp_path, capsys):
    path = store(tmp_path/'store # with spaces.sqlite')
    before = path.read_bytes()
    assert _effectivecli.main(['--db', str(path), 'ls', 'ware']) == 0
    assert 'ore' in capsys.readouterr().out
    assert _effectivecli.main(['--db', str(path), 'sql', 'SELECT * FROM nonexistent']) == 1
    assert _effectivecli.main(['--db', str(path), 'sql', 'DELETE FROM entities']) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize('detail', [['invalid'], [{}], [{'folder': []}]])
@pytest.mark.parametrize('command', [('ls', 'ware'), ('sql', 'SELECT 1')])
def test_malformed_freshness_metadata_refused(tmp_path, capsys, monkeypatch, detail, command):
    path = store(tmp_path/'bad-metadata.sqlite')
    with sqlite3.connect(path) as con:
        _freshness.stamp_sqlite(con, dict(content='before', engine='engine',
            reference='before-reference', detail=detail))
    monkeypatch.setattr(_freshness, 'fingerprint', lambda *a, **kw:
        dict(content='now', engine='engine', reference='now-reference',
             detail=[dict(folder='current', root='root', files=0)]))
    before = path.read_bytes()
    assert _effectivecli.main(['--db', str(path), *command]) == 2
    err = capsys.readouterr().err
    assert 'freshness metadata' in err and 'Rebuild' in err and 'Traceback' not in err
    assert path.read_bytes() == before


def test_valid_stale_metadata_remains_readable(tmp_path, capsys, monkeypatch):
    path = store(tmp_path/'stale-metadata.sqlite')
    with sqlite3.connect(path) as con:
        _freshness.stamp_sqlite(con, dict(content='before', engine='engine',
            reference='before-reference', detail=[dict(folder='previous', root='root', files=0)]))
    monkeypatch.setattr(_freshness, 'fingerprint', lambda *a, **kw:
        dict(content='now', engine='engine', reference='now-reference',
             detail=[dict(folder='current', root='root', files=0)]))
    before = path.read_bytes()
    assert _effectivecli.main(['--db', str(path), 'ls', 'ware']) == 0
    cap = capsys.readouterr()
    assert 'ore' in cap.out and 'STALE' in cap.err
    assert path.read_bytes() == before


def test_a_BROKEN_PIPE_is_not_relabelled_store_unreadable(tmp_path, capsys, monkeypatch):
    """v4.0.0 review R5-2: the dispatcher caught OSError/ValueError around EVERY command, so
    `x4effective ls ware | head` (BrokenPipeError is an OSError) printed 'effective store
    unreadable ... Rebuild' -- sending the user to rebuild a healthy store."""
    path = store(tmp_path/'ok.sqlite')
    def pipe(con, args):
        raise _closedpipe.OutputClosed(32, 'Broken pipe')
    monkeypatch.setattr(_effectivecli, '_cmd_ls', pipe)
    rc = _effectivecli.main(['--db', str(path), 'ls', 'ware'])
    err = capsys.readouterr().err
    assert 'unreadable' not in err, err
    assert rc == 0


def test_a_command_BUG_ValueError_propagates_and_is_not_relabelled(tmp_path, capsys, monkeypatch):
    path = store(tmp_path/'ok.sqlite')
    def bug(con, args):
        raise ValueError('a bug in the command, not in the store')
    monkeypatch.setattr(_effectivecli, '_cmd_ls', bug)
    with pytest.raises(ValueError, match='a bug in the command'):
        _effectivecli.main(['--db', str(path), 'ls', 'ware'])
    assert 'unreadable' not in capsys.readouterr().err


def test_a_UNC_store_path_gets_a_URI_sqlite_accepts():
    """v4.0.0 review R5-3: Path.as_uri() spells a UNC path `file://server/share/...`, and
    SQLite refuses a URI authority ('invalid uri authority'), so a store on a network share
    could never be opened. The authority is folded into the path: `file:////server/share/...`."""
    from pathlib import PureWindowsPath
    bs = chr(92)
    unc = PureWindowsPath(bs * 2 + 'fileserver' + bs + 'share' + bs + 'effective.sqlite')
    assert _effective._sqlite_ro_uri(unc) == 'file:////fileserver/share/effective.sqlite?mode=ro'
    drive = PureWindowsPath('C:' + bs + 'x' + bs + 'effective.sqlite')
    assert _effective._sqlite_ro_uri(drive) == 'file:///C:/x/effective.sqlite?mode=ro'


def test_a_store_REACHED_THROUGH_a_UNC_path_opens(tmp_path, capsys):
    """MEASURED on Windows through the local admin share: the plain URI 'unable to open
    database file', the folded one opens. Skipped where no admin share is reachable."""
    import os
    if os.name != 'nt':
        pytest.skip('UNC paths are a Windows spelling')
    path = store(tmp_path/'unc.sqlite')
    bs = chr(92)
    drive, rest = os.path.splitdrive(str(path))
    unc = bs * 2 + 'localhost' + bs + drive[0] + '$' + rest
    if not os.path.exists(unc):
        pytest.skip('no reachable local admin share -- not checked')
    assert _effectivecli.main(['--db', unc, 'ls', 'ware']) == 0
    assert 'ore' in capsys.readouterr().out

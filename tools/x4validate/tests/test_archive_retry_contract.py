"""Exclusive-name retries and unverifiable archive discovery retain evidence."""
import io
from types import SimpleNamespace

from x4validate import _debugcli as D, _livecli as C
from test_livearchive import big_uidata


def test_debug_reservation_collision_retries_without_replacing(tmp_path, monkeypatch):
    log = tmp_path / "debug.txt"
    log.write_bytes(b"owned capture\n")
    dest = tmp_path / "archive"
    dest.mkdir()
    stamp = D.datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%dT%H%M")
    foreign = dest / f"debug-{stamp}-first.txt"
    foreign.write_bytes(b"independent capture")
    tokens = iter(["first", "second"])
    monkeypatch.setattr(D.uuid, "uuid4", lambda: SimpleNamespace(hex=next(tokens)))
    monkeypatch.setattr(D._paths, "game_extensions", lambda: None)
    result = D.archive(log, dest)
    assert result.name.endswith("-second.txt")
    assert result.read_bytes() == log.read_bytes()
    assert foreign.read_bytes() == b"independent capture"
    assert D.read_archive_meta(result)["degraded"] is True


def test_live_stage_collision_retries_without_replacing(tmp_path, monkeypatch):
    src = tmp_path / "uidata.xml"
    src.write_text(big_uidata(), encoding="utf-8")
    dest = tmp_path / "archive"
    dest.mkdir()
    real_open = C.Path.open
    collided = []

    def opening(path, mode="r", *args, **kwargs):
        if mode == "xb" and path.suffix == ".partial" and not collided:
            path.write_bytes(b"independent partial capture")
            collided.append(path)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(C.Path, "open", opening)
    assert C.cmd_archive(str(src), str(dest), out=io.StringIO()) == 0
    assert collided[0].read_bytes() == b"independent partial capture"
    archives = list(dest.glob("*.uidata.xml"))
    assert len(archives) == 1 and archives[0].read_bytes() == src.read_bytes()


def test_unreadable_archive_discovery_does_not_certify_preservation(tmp_path, monkeypatch):
    src = tmp_path / "uidata.xml"
    src.write_text(big_uidata(), encoding="utf-8")
    reports = tmp_path / "_reports"
    reports.mkdir()
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    real_glob = C.Path.glob

    def listing(path, pattern, *args, **kwargs):
        if path == reports:
            raise OSError("owned discovery unavailable")
        return real_glob(path, pattern, *args, **kwargs)

    monkeypatch.setattr(C.Path, "glob", listing)
    assert C._verified_archive(reports, C._dump_sha(str(src))) is None
    buf = io.StringIO()
    C._archive_hint(str(src), buf)
    assert "no verified archive found" in buf.getvalue() and "OVERWRITES" in buf.getvalue()
    buf = io.StringIO()
    assert C.cmd_archive(str(src), str(reports), out=buf) == 0
    assert "already archived" not in buf.getvalue() and "byte-identical" in buf.getvalue()
    files = list(reports.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == src.read_bytes()

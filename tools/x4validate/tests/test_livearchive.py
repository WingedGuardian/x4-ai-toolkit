"""`x4live archive` — get the ground truth OUT of a file the game overwrites.

WHY THIS COMMAND EXISTS, recorded here because the reason is the design. The engine's
resolved values are the only ground truth we have for what our merged tree *should*
say. They cost a play session to produce. And they live in `{profile}/uidata.xml`,
which X4 rewrites on every exit.

One was already lost that way: the probe was removed, the game exited, the file was
rewritten, and the fixture for the F72 traversals went with it — leaving four example
values that survived only because someone had quoted them into a document. **A file
the game owns is not storage.**

So the contract under test is narrow and blunt: it must copy BYTES, refuse anything it
cannot decode first, refuse anything implausibly small, and prove the copy by reading
it back. Every one of those has a twin below that makes it go red.
"""
from __future__ import annotations

import io

import pytest

from x4validate import _livecli as C
from x4validate import _livedump as L
from test_livedump import good_rows, uidata


def big_uidata(n: int = 400) -> str:
    """A fixture ABOVE the size floor, so the floor stays real in production.

    The floor (4096 bytes) exists to reject a truncated read. Lowering it to suit a
    small fixture would have made every test pass while the guard protected nothing —
    so the fixture grows instead of the guard shrinking.
    """
    rows = [["HDR", "schema=2", "probe=test", "elapsed=1.5"], ["EXT_FIELDS", "id,enabled"]]
    rows += [["EXT", f"mod_{i}", "true", "x" * 40] for i in range(n)]
    rows.append(["END", str(len(rows) + 1)])
    return uidata(rows)


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "uidata.xml"
    p.write_text(big_uidata(), encoding="utf-8")
    assert p.stat().st_size > 4096, "fixture must clear the size floor"
    return p


def run(src_path, out_dir, buf=None):
    buf = buf or io.StringIO()
    rc = C.cmd_archive(str(src_path), str(out_dir), out=buf)
    return rc, buf.getvalue()


# --------------------------------------------------------------------------- #
# the copy itself
# --------------------------------------------------------------------------- #

def test_archive_writes_a_BYTE_IDENTICAL_copy(src, tmp_path):
    d = tmp_path / "arch"
    rc, out = run(src, d)
    assert rc == 0
    files = list(d.glob("livedump-*.uidata.xml"))
    assert len(files) == 1
    assert files[0].read_bytes() == src.read_bytes(), "the archive is not a faithful copy"
    assert "byte-identical" in out


def test_the_archive_REPLAYS_through_the_normal_reader(src, tmp_path):
    """The point of copying the whole file rather than the decoded payload.

    If this fails, the archive is a museum piece: bytes preserved, unusable. Storing
    the decoded payload would need three escaping layers re-applied to read it back —
    which is precisely the code most likely to be wrong, and it would be wrong in the
    one place we would never notice.
    """
    d = tmp_path / "arch"
    run(src, d)
    archived = next(d.glob("livedump-*.uidata.xml"))
    replay = io.StringIO()
    assert C.cmd_dump(str(archived), out=replay) == 0
    assert "rows" in replay.getvalue()


def test_re_archiving_the_same_dump_is_a_NO_OP(src, tmp_path):
    """Content-addressed on the PAYLOAD, so running it twice does not accumulate.

    An archive full of near-duplicates is one nobody can quote from with confidence —
    "which of these five is the one the report cited?" is a question with no answer.
    """
    d = tmp_path / "arch"
    assert run(src, d)[0] == 0
    rc, out = run(src, d)
    assert rc == 0
    assert "already archived" in out and "Nothing written" in out
    assert len(list(d.glob("livedump-*.uidata.xml"))) == 1


def test_a_DIFFERENT_dump_archives_alongside_rather_than_overwriting(src, tmp_path):
    """The twin of the no-op test. If identity were per-file rather than per-payload,
    a second capture would silently replace the first and the loss would be invisible."""
    d = tmp_path / "arch"
    run(src, d)
    other = tmp_path / "other.xml"
    other.write_text(big_uidata(401), encoding="utf-8")
    assert run(other, d)[0] == 0
    assert len(list(d.glob("livedump-*.uidata.xml"))) == 2


@pytest.mark.parametrize("body", ["truncated", big_uidata(401)], ids=["truncated", "different-payload"])
def test_a_matching_filename_without_matching_payload_is_not_an_archive(src, tmp_path, monkeypatch, body):
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    d = tmp_path / "_reports"
    d.mkdir()
    sha = C._dump_sha(str(src))[:8]
    old = d / f"livedump-previous-{sha}.uidata.xml"
    old.write_text(body, encoding="utf-8")
    previous = old.read_bytes()
    hint = io.StringIO()
    C._archive_hint(str(src), hint)
    assert "no verified archive found" in hint.getvalue()
    rc, output = run(src, d)
    assert rc == 0 and "already archived" not in output
    assert old.read_bytes() == previous
    assert len(list(d.glob("livedump-*.uidata.xml"))) == 2
    hint = io.StringIO()
    C._archive_hint(str(src), hint)
    assert hint.getvalue() == ""


def test_publication_collision_preserves_existing_file(src, tmp_path, monkeypatch):
    d = tmp_path / "arch"
    real_link = C.os.link
    collided = []
    def race(source, destination, *args, **kwargs):
        if not collided:
            C.Path(destination).write_bytes(b"independent capture")
            collided.append(C.Path(destination))
        return real_link(source, destination, *args, **kwargs)
    monkeypatch.setattr(C.os, "link", race)
    assert run(src, d)[0] == 0
    assert collided and collided[0].read_bytes() == b"independent capture"
    assert any(p.read_bytes() == src.read_bytes() for p in d.glob("livedump-*.uidata.xml"))
    assert not list(d.glob("*.partial"))


def test_archive_validates_and_preserves_one_source_snapshot(src, tmp_path, monkeypatch):
    original = src.read_bytes()
    real_bytes, real_text = C.Path.read_bytes, C.Path.read_text
    reads = []
    def snapshot(self):
        if self == src:
            reads.append("bytes")
            return original if len(reads) == 1 else b"changed source"
        return real_bytes(self)
    def changing_text(self, *args, **kwargs):
        if self == src:
            reads.append("text")
            return big_uidata(401)
        return real_text(self, *args, **kwargs)
    monkeypatch.setattr(C.Path, "read_bytes", snapshot)
    monkeypatch.setattr(C.Path, "read_text", changing_text)
    d = tmp_path / "arch"
    assert run(src, d)[0] == 0
    assert reads == ["bytes"]
    archived = next(d.glob("livedump-*.uidata.xml"))
    assert real_bytes(archived) == original
    assert archived.name.endswith("-" + C._dump_sha(str(archived))[:8] + ".uidata.xml")


def test_no_hard_links_falls_back_to_an_exclusive_verified_copy(src, tmp_path, monkeypatch):
    """A volume without hard links (exFAT, some network shares) still archives: the
    final name is CREATED EXCLUSIVELY (never overwriting anything) and re-read. Until the
    v4.0 review (V7) this refused outright, so the ground truth stayed in the file the
    game overwrites."""
    def unsupported(*args, **kwargs):
        raise OSError("owned fixture: hard links unavailable")
    monkeypatch.setattr(C.os, "link", unsupported)
    d = tmp_path / "arch"
    rc, out = run(src, d)
    assert rc == 0, out
    archived = list(d.glob("livedump-*.uidata.xml"))
    assert len(archived) == 1 and archived[0].read_bytes() == src.read_bytes()
    assert not list(d.glob("*.partial"))
    assert "hard link" in out
    assert src.is_file()


def test_TWIN_the_fallback_never_overwrites_an_existing_final_name(src, tmp_path, monkeypatch):
    monkeypatch.setattr(C.os, "link",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no hard links")))
    d = tmp_path / "arch"
    d.mkdir()
    names = iter(["taken", "taken", "free"])
    monkeypatch.setattr(C.uuid, "uuid4", lambda: type("U", (), {"hex": next(names)})())
    sha = C._dump_sha(str(src))
    foreign = d / f"livedump-X-taken-{sha[:8]}.uidata.xml"
    monkeypatch.setattr(C.datetime, "datetime",
                        type("D", (), {"now": staticmethod(
                            lambda: type("N", (), {"strftime": lambda self, f: "X"})())}))
    foreign.write_bytes(b"foreign evidence")
    rc, _out = run(src, d)
    assert rc == 0
    assert foreign.read_bytes() == b"foreign evidence"
    assert (d / f"livedump-X-free-{sha[:8]}.uidata.xml").read_bytes() == src.read_bytes()


def test_a_MISSING_source_is_refused_rc2_not_a_traceback(tmp_path, capsys):
    """V7 (regression from 5398907): a raw FileNotFoundError ended the run, rc 1."""
    missing = tmp_path / "nope" / "uidata.xml"
    d = tmp_path / "arch"
    with pytest.raises(L.LiveDumpUnavailable, match="does not exist"):
        run(missing, d)
    rc = C.main(["--file", str(missing), "archive", "--out", str(d)])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err
    assert not d.exists()


# --------------------------------------------------------------------------- #
# the refusals — each its own twin
# --------------------------------------------------------------------------- #

def test_a_file_it_cannot_DECODE_is_refused_before_anything_is_copied(tmp_path):
    """Parse first, copy second.

    Archiving bytes we cannot decode would preserve the file and lose the only thing
    that makes it evidence — and it would report success while doing it.
    """
    bad = tmp_path / "uidata.xml"
    bad.write_text(uidata(None, payload="HDR\tschema=2\nEXT\ta"), encoding="utf-8")
    d = tmp_path / "arch"
    with pytest.raises((L.LiveDumpCorrupt, L.LiveDumpUnavailable)):
        run(bad, d)
    assert not d.exists() or not list(d.glob("livedump-*")), "copied despite refusing"


def test_an_IMPLAUSIBLY_SMALL_file_is_refused_as_a_truncated_read(tmp_path):
    """A real uidata.xml carrying a dump is hundreds of kilobytes.

    Without a floor, a truncated read archives cleanly and becomes the thing a future
    session quotes — the small-answer-vs-non-answer confusion, made permanent.
    """
    small = tmp_path / "uidata.xml"
    small.write_text(uidata(good_rows(1)), encoding="utf-8")
    assert small.stat().st_size < 4096, "fixture must be BELOW the floor to test it"
    d = tmp_path / "arch"
    with pytest.raises(L.LiveDumpCorrupt, match="truncated read"):
        run(small, d)


def test_no_archive_directory_is_a_NON_ANSWER_not_a_silent_skip(src, monkeypatch):
    monkeypatch.setattr(C._paths, "mods", lambda: None)
    with pytest.raises(L.LiveDumpUnavailable, match="nowhere to put this"):
        C.cmd_archive(str(src), None, out=io.StringIO())


# --------------------------------------------------------------------------- #
# the advisory — it must fire, and it must STOP firing
# --------------------------------------------------------------------------- #

def test_the_hint_FIRES_when_the_dump_is_not_archived(src, tmp_path, monkeypatch):
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    buf = io.StringIO()
    C._archive_hint(str(src), buf)
    assert "no verified archive found" in buf.getvalue()
    assert "OVERWRITES" in buf.getvalue()


def test_the_hint_STOPS_once_it_is_archived(src, tmp_path, monkeypatch):
    """The falsification twin for the hint.

    A reminder that always fires is one you learn to scroll past, which makes it worse
    than none — it trains you to ignore the channel it shares with real findings.
    """
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    C.cmd_archive(str(src), None, out=io.StringIO())
    buf = io.StringIO()
    C._archive_hint(str(src), buf)
    assert buf.getvalue() == "", f"hint still firing after archiving: {buf.getvalue()!r}"


def test_the_hint_is_SILENT_when_it_cannot_tell(tmp_path, monkeypatch):
    """An advisory that raises turns "I could not check whether you have a backup"
    into a failure of the command you actually ran. It has no findings to report, so
    it must have no failure modes either."""
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    buf = io.StringIO()
    C._archive_hint(str(tmp_path / "does_not_exist.xml"), buf)
    assert buf.getvalue() == ""


def test_a_FAILED_write_leaves_NO_file_under_the_final_name(src, tmp_path, monkeypatch):
    """The falsification twin for atomicity, and it guards a nasty second-order bug.

    A direct write that failed partway would leave a file whose NAME already satisfies
    the content-address glob. The next run would then report "already archived" and the
    unarchived reminder would fall silent — both for a TRUNCATED file. A partial
    artifact that cannot say what it is is worse than an absence, because it reports
    success.

    Simulated by making the read-back disagree, which is the one failure the command
    can actually detect.
    """
    d = tmp_path / "arch"
    real = C.Path.read_bytes

    def lying_read(self):
        # Lie about ANY file the command writes into the archive, not just the
        # `.partial`. Naming `.partial` specifically would couple the test to the fix:
        # against the OLD direct-write code that name never existed, so the lie would
        # never fire and this would fail with "DID NOT RAISE" — a red light for the
        # wrong reason, proving nothing about leftover files. As written, BOTH
        # implementations raise, and only the old one leaves a file behind.
        return b"" if self.name.startswith("livedump-") else real(self)

    monkeypatch.setattr(C.Path, "read_bytes", lying_read)
    with pytest.raises(L.LiveDumpCorrupt, match="read back"):
        run(src, d)

    assert not list(d.glob("livedump-*.uidata.xml")), "a partial write kept the final name"
    assert not list(d.glob("*.partial")), "a .partial was left behind to be puzzled over"


def test_a_failed_write_does_not_silence_the_reminder(src, tmp_path, monkeypatch):
    """The consequence the test above exists to prevent, asserted directly.

    If a partial file could take the final name, this is what the user would see: no
    warning, and a truncated archive standing in for the real one.
    """
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    real = C.Path.read_bytes
    monkeypatch.setattr(C.Path, "read_bytes",
                        lambda self: b"" if self.name.endswith(".partial") else real(self))
    with pytest.raises(L.LiveDumpCorrupt):
        C.cmd_archive(str(src), None, out=io.StringIO())
    monkeypatch.undo()

    buf = io.StringIO()
    monkeypatch.setattr(C._paths, "mods", lambda: tmp_path)
    C._archive_hint(str(src), buf)
    assert "no verified archive found" in buf.getvalue(), "the reminder went quiet after a FAILED archive"

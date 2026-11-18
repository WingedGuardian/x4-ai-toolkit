"""`x4live save SLOT`, host side: never-overwrite, one slot, verify by READING the slot.

User decision 2026-11-18: a plain CLI capability like `query`/`equipment`, with no
automation journal and no recorded-conversation authority. These tests pin the protection
that stays: an EMPTY standard slot only, refused before anything is sent; after the reply,
the new save must exist and every other file in the save folder must be byte-identical; a
lost reply is resolved by reading the slot, never by sending again.

Each refusal clause has its own test, and each starts from the SAME passing baseline
(`_happy`), so the baseline is the falsification twin of every clause: one mutation, one
refusal. Synthetic files only; nothing here reads a real save folder.
"""
from __future__ import annotations

import contextlib
import gzip
import os
import re

import pytest

from x4validate import _livecli as C
from x4validate import _livepipe, _livesave

REQ = re.compile(r"^[0-9a-f]{32}$")
HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n<savegame>\n<info>\n'
          '<save name="{name}" date="1721947652"/>\n'
          '<game id="X4" version="900" build="1" modified="0" time="1.0" start="custom_creative"/>\n'
          '<player name="p" location="l" money="1"/>\n</info>\n<universe/>\n</savegame>\n')


def _gz(path, name, extra=b""):
    path.write_bytes(gzip.compress(HEADER.format(name=name).encode() + extra))


class Clock:
    """Fake monotonic clock; `sleep` advances it and runs scheduled engine effects."""

    def __init__(self):
        self.t, self.sleeps, self.hooks = 0.0, 0, []

    def clock(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        self.sleeps += 1
        for hook in list(self.hooks):
            hook(self)


class Engine:
    """Answers `save` like the helper does and, unless told otherwise, writes the slot."""

    path = "stub-pipe"

    def __init__(self, save_dir, clock, *, status="OK", acted="yes", reason="requested",
                 write=True, label_override=None, raises=None, advisory=True, side=None,
                 adv_label=None):
        self.save_dir, self.clock = save_dir, clock
        self.status, self.acted, self.reason = status, acted, reason
        self.write, self.label_override, self.raises = write, label_override, raises
        self.advisory, self.side, self.adv_label = advisory, side, adv_label
        self.asks = []

    def ask(self, verb, *args):
        self.asks.append((verb, args))
        slot, req = args if len(args) == 2 else (None, None)
        label = f"x4live-{req}"
        if self.write:
            _gz(self.save_dir / f"{slot}.xml.gz", self.label_override or label)
        if self.side:
            self.side(self.save_dir)
        if self.raises is not None:
            raise self.raises
        adv = (f"write=yes verb=save reason={self.reason} acted={self.acted} slot={slot} "
               f"label={self.adv_label or label} build=deadbeef")
        payload = (adv + "\tprose") if self.advisory else "prose only"
        return _livepipe.Reply(seq=1, status=self.status, payload=payload)


@pytest.fixture
def world(tmp_path, monkeypatch):
    save = tmp_path / "save"
    save.mkdir()
    _gz(save / "save_001.xml.gz", "#001")
    _gz(save / "quicksave.xml.gz", "#q")
    (save / "steam_autocloud.vdf").write_bytes(b"x" * 51)
    clk = Clock()
    monkeypatch.setattr(C._paths, "savegames", lambda: save)
    monkeypatch.setattr(_livesave, "_clock", clk.clock)
    monkeypatch.setattr(_livesave, "_sleep", clk.sleep)
    return save, clk


def _serve(monkeypatch, engine):
    @contextlib.contextmanager
    def fake(p, timeout):
        yield engine

    monkeypatch.setattr(C, "_live_open", fake)


def _never_open(monkeypatch):
    def refuse(p, timeout):
        raise AssertionError("the pipe was opened for a request refused offline")

    monkeypatch.setattr(C, "_live_open", refuse)


def _run(capsys, *argv):
    rc = C.main(["save", *argv])
    out, err = capsys.readouterr()
    return rc, out, err


def _happy(world, monkeypatch, **kw):
    save, clk = world
    engine = Engine(save, clk, **kw)
    _serve(monkeypatch, engine)
    return engine


# --------------------------------------------------------------------------- baseline

def test_an_empty_slot_is_saved_and_verified(world, monkeypatch, capsys):
    engine = _happy(world, monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert len(engine.asks) == 1
    verb, (slot, req) = engine.asks[0]
    assert (verb, slot) == ("save", "save_004") and REQ.match(req)
    assert "verified" in out and "save_004.xml.gz" in out
    assert "3 other file(s) unchanged" in out


def test_the_banner_goes_to_stderr_and_says_it_writes_a_save(world, monkeypatch, capsys):
    _happy(world, monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0
    assert "WRITE" in err and "save" in err and "not authenticated" in err
    assert "!!!!" not in out


# --------------------------------------------------------------------------- slot argument

@pytest.mark.parametrize("slot", ["save_000", "save_011", "save_*", "save_00?", "save_1",
                                  "SAVE_001", "save_001.xml.gz", "quicksave", "autosave_01",
                                  "save_004 ", "../save_004", ""])
def test_anything_but_one_standard_slot_is_refused_before_sending(slot, world, monkeypatch,
                                                                  capsys):
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, slot)
    assert rc == 2, out + err
    assert "save_001" in err and "save_010" in err


def test_two_slots_are_refused(world, monkeypatch, capsys):
    _never_open(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        C.main(["save", "save_004", "save_005"])
    assert exc.value.code == 2


@pytest.mark.parametrize("slot", ["save_001", "save_009", "save_010"])
def test_the_edges_of_the_slot_range_are_accepted(slot, world, monkeypatch, capsys):
    """Twin of the refusal above: a pattern that refused everything would pass it."""
    save, _ = world
    (save / "save_001.xml.gz").unlink()
    _happy(world, monkeypatch)
    rc, out, err = _run(capsys, slot)
    assert rc == 0, out + err


@pytest.mark.parametrize("argv", [("--settle-timeout", "0"), ("--settle-timeout", "-1"),
                                  ("--settle-timeout", "nan"), ("--settle-timeout", "3601"),
                                  ("--settle", "0.1"), ("--settle", "61"), ("--settle", "inf")])
def test_timing_arguments_out_of_bounds_are_refused_before_sending(argv, world, monkeypatch,
                                                                   capsys):
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_004", *argv)
    assert rc == 2 and argv[0] in err, out + err


@pytest.mark.parametrize("argv", [("--settle-timeout", "3600"), ("--settle", "0.5"),
                                  ("--settle", "60")])
def test_timing_arguments_at_their_bounds_are_accepted(argv, world, monkeypatch, capsys):
    _happy(world, monkeypatch)
    rc, out, err = _run(capsys, "save_004", *argv)
    assert rc == 0, out + err


# --------------------------------------------------------------------------- occupancy

def test_an_occupied_slot_is_refused_before_sending(world, monkeypatch, capsys):
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_001")
    assert rc == 2
    assert "occupied" in err and "save_001.xml.gz" in err


@pytest.mark.parametrize("name", ["save_004.xml", "save_004.xml - Copy.gz", "SAVE_004.XML.GZ",
                                  "save_004.xml.gz.tmp"])
def test_any_member_related_to_the_slot_counts_as_occupied(name, world, monkeypatch, capsys):
    """SlotVerifier.reserve's rule: an unknown variant (a copy, a temp, another case) of
    the slot refuses rather than being guessed harmless."""
    save, _ = world
    (save / name).write_bytes(b"x")
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and "occupied" in err, out + err


def test_save_010_is_not_occupied_by_save_001(world, monkeypatch, capsys):
    """Twin: the prefix rule must not make `save_01` style prefixes collide."""
    _happy(world, monkeypatch)
    rc, out, err = _run(capsys, "save_010")
    assert rc == 0, out + err


def test_an_unconfigured_save_folder_is_refused(world, monkeypatch, capsys):
    monkeypatch.setattr(C._paths, "savegames", lambda: None)
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and "X4_SAVES" in err


def test_a_missing_save_folder_is_refused(world, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(C._paths, "savegames", lambda: tmp_path / "nowhere")
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2


# --------------------------------------------------------------------------- engine answer

def test_an_engine_refusal_that_changed_nothing_is_a_finding(world, monkeypatch, capsys):
    engine = _happy(world, monkeypatch, status="ERR", acted="no", reason="occupied",
                    write=False)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 1, out + err
    assert "reason=occupied" in out and len(engine.asks) == 1


def test_an_engine_refusal_while_the_folder_changed_is_untrusted(world, monkeypatch, capsys):
    def touch(save):
        (save / "quicksave.xml.gz").write_bytes(b"changed")
    _happy(world, monkeypatch, status="ERR", acted="no", reason="not-saveable",
           write=False, side=touch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "quicksave.xml.gz" in out, out + err


def test_a_reply_without_its_advisory_is_untrusted_even_if_a_save_landed(world, monkeypatch,
                                                                         capsys):
    engine = _happy(world, monkeypatch, advisory=False)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3, out + err
    assert len(engine.asks) == 1


def test_a_save_the_engine_attempted_but_raised_is_still_read_back(world, monkeypatch, capsys):
    _happy(world, monkeypatch, status="ERR", acted="yes", reason="raised", write=False)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3, out + err
    assert "not resend" in (out + err).lower() or "never resent" in (out + err).lower()


def test_a_refusal_for_ANOTHER_request_is_not_taken_as_this_ones(world, monkeypatch, capsys):
    """An advisory naming a different label is out of step: it cannot vouch that THIS
    request changed nothing, so the slot is read and the outcome is untrusted (3), not 1."""
    _happy(world, monkeypatch, status="ERR", acted="no", reason="occupied", write=False,
           adv_label="x4live-" + "f" * 32)
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert rc == 3, out + err


def test_an_ERR_reply_that_acted_is_untrusted_even_when_the_save_landed(world, monkeypatch,
                                                                       capsys):
    _happy(world, monkeypatch, status="ERR", acted="yes", reason="raised")
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3, out + err
    assert "save name=x4live-" in out, "the slot must still be read and reported"


# --------------------------------------------------------------------------- after the reply

def test_a_changed_bystander_is_named_and_exits_3(world, monkeypatch, capsys):
    def edit(save):
        p = save / "save_001.xml.gz"
        data = bytearray(p.read_bytes())
        data[-1] ^= 1  # same size, different bytes
        st = p.stat()
        p.write_bytes(bytes(data))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    _happy(world, monkeypatch, side=edit)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3, out + err
    assert "changed: save_001.xml.gz" in out


def test_a_removed_bystander_is_named_and_exits_3(world, monkeypatch, capsys):
    _happy(world, monkeypatch, side=lambda save: (save / "quicksave.xml.gz").unlink())
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "removed: quicksave.xml.gz" in out, out + err


def test_an_extra_new_file_is_named_and_exits_3(world, monkeypatch, capsys):
    _happy(world, monkeypatch, side=lambda save: (save / "autosave_01.xml.gz").write_bytes(b"a"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "added: autosave_01.xml.gz" in out, out + err


def test_a_save_carrying_another_label_is_untrusted(world, monkeypatch, capsys):
    _happy(world, monkeypatch, label_override="#004")
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "label" in out, out + err


def test_a_slot_that_never_appears_is_unverified_not_resent(world, monkeypatch, capsys):
    engine = _happy(world, monkeypatch, write=False)
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert rc == 3, out + err
    assert len(engine.asks) == 1
    assert "do not resend" in (out + err).lower()


def test_a_slot_still_being_written_is_waited_for(world, monkeypatch, capsys):
    """A truncated gzip is a save in progress, not a failure: the CRC decides."""
    save, clk = world
    engine = _happy(world, monkeypatch, write=False)
    full = {}

    def ask(verb, *args):
        engine.asks.append((verb, args))
        slot, req = args
        whole = gzip.compress(HEADER.format(name=f"x4live-{req}").encode() + b"x" * 4096)
        full["data"], full["path"] = whole, save / f"{slot}.xml.gz"
        full["path"].write_bytes(whole[: len(whole) // 2])
        return _livepipe.Reply(seq=1, status="OK", payload=(
            f"write=yes verb=save reason=requested acted=yes slot={slot} label=x4live-{req} "
            "build=deadbeef\tprose"))

    def finish(c):
        if c.sleeps == 12:
            full["path"].write_bytes(full["data"])
    engine.ask = ask
    clk.hooks.append(finish)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert clk.sleeps > 12


# --------------------------------------------------------------------------- lost reply

def test_a_lost_reply_is_resolved_by_reading_the_slot(world, monkeypatch, capsys):
    engine = _happy(world, monkeypatch,
                    raises=_livepipe.LiveQueryUnavailable("no reply within 1s"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert len(engine.asks) == 1, "a lost reply must never be answered by resending"
    assert "reply was lost" in (out + err).lower()


def test_a_lost_reply_with_no_save_is_unverified(world, monkeypatch, capsys):
    engine = _happy(world, monkeypatch, write=False,
                    raises=_livepipe.LiveQueryDegraded("checksum mismatch"))
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert rc == 3, out + err
    assert len(engine.asks) == 1
    assert "do not resend" in (out + err).lower()


def test_a_game_that_never_connected_sent_nothing(world, monkeypatch, capsys):
    def unreachable(p, timeout):
        raise _livepipe.LiveQueryUnavailable("the game never connected")

    monkeypatch.setattr(C, "_live_open", unreachable)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2
    assert "may already" not in err.lower()


# --------------------------------------------------------------------------- vocabulary

def test_query_refuses_save_before_opening_the_pipe(world, monkeypatch, capsys):
    _never_open(monkeypatch)
    assert C.main(["query", "save", "save_004", "a" * 32]) == 2
    assert "x4live save" in capsys.readouterr().err


def test_the_write_vocabulary_is_pause_unpause_and_save():
    assert set(C.WRITE_VERBS) == {"pause", "unpause", "save"}
    assert set(C.PAUSE_VERBS) == {"pause", "unpause"}


def test_the_slot_rule_is_the_slot_verifiers_rule():
    from x4validate import _livepersistence as P
    assert _livesave.SLOT is P.STANDARD_SAVE_SLOT
    assert [s for s in (f"save_{i:03}" for i in range(0, 13)) if _livesave.SLOT.fullmatch(s)] == [
        f"save_{i:03}" for i in range(1, 11)]

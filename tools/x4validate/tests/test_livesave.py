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


def test_a_changed_steam_autocloud_vdf_is_reported_but_passes(world, monkeypatch, capsys):
    """User decision 2026-11-18: Steam rewrites this ONE top-level file; report, don't fail."""
    _happy(world, monkeypatch,
           side=lambda save: (save / "steam_autocloud.vdf").write_bytes(b"y" * 60))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert "steam_autocloud.vdf changed (reported, not a failure" in out


def test_the_autocloud_exception_matches_case_insensitively(world, monkeypatch, capsys):
    save, _ = world
    (save / "steam_autocloud.vdf").rename(save / "Steam_AutoCloud.VDF")
    _happy(world, monkeypatch,
           side=lambda save: (save / "Steam_AutoCloud.VDF").write_bytes(b"y"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err


def test_autocloud_plus_another_change_still_exits_3(world, monkeypatch, capsys):
    def both(save):
        (save / "steam_autocloud.vdf").write_bytes(b"y")
        (save / "quicksave.xml.gz").write_bytes(b"changed")
    _happy(world, monkeypatch, side=both)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "changed: quicksave.xml.gz" in out, out + err


@pytest.mark.parametrize("rel", ["sub/steam_autocloud.vdf", "steam_autocloud.vdf.bak",
                                 "steam_autocloud.vdf2"])
def test_a_file_merely_named_like_autocloud_is_not_excepted(rel, world, monkeypatch, capsys):
    save, _ = world
    target = save / rel
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"x")
    _happy(world, monkeypatch, side=lambda s: (s / rel).write_bytes(b"changed"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and f"changed: {rel}" in out, out + err


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


# --------------------------------------------------------------------------- 4.3 review D1-D3


def _serve_opening(monkeypatch, engine, during_open):
    """A pipe whose connect takes a while (`_live_open` can wait up to 15 s for the game),
    during which `during_open()` happens to the save folder."""
    @contextlib.contextmanager
    def fake(p, timeout):
        during_open()
        yield engine

    monkeypatch.setattr(C, "_live_open", fake)


def test_RED_a_save_landing_in_the_slot_while_the_pipe_opens_is_refused_unsent(world, monkeypatch,
                                                                              capsys):
    """Two concurrent `x4live save save_004`: the other run's save lands while this one is
    still connecting. The occupancy check must be re-taken right before asking."""
    save, clk = world
    engine = Engine(save, clk)
    _serve_opening(monkeypatch, engine, lambda: _gz(save / "save_004.xml.gz", "x4live-other"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and "occupied" in err, out + err
    assert engine.asks == [], "an occupied slot must never be asked for"
    assert "Nothing was sent" in err


def test_TWIN_a_bystander_change_while_the_pipe_opens_is_not_blamed_on_the_save(world, monkeypatch,
                                                                              capsys):
    """The manifest is re-taken with the occupancy check, so a change BEFORE the request
    (an autosave while connecting) is not reported as one the save made."""
    save, clk = world
    engine = Engine(save, clk)
    _serve_opening(monkeypatch, engine, lambda: (save / "quicksave.xml.gz").write_bytes(b"new"))
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert len(engine.asks) == 1


def test_TWIN_a_folder_unreadable_while_the_pipe_opens_is_refused_unsent(world, monkeypatch, capsys):
    save, clk = world
    engine = Engine(save, clk)
    real, calls = _livesave.snapshot, []

    def second_fails(d):
        calls.append(d)
        if len(calls) == 2:
            raise _livesave.PersistenceUnavailable("linked route (test)")
        return real(d)
    monkeypatch.setattr(_livesave, "snapshot", second_fails)
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and "cannot be read safely" in err and engine.asks == [], out + err
    assert len(calls) == 2


class LoadingEngine(Engine):
    """Answers `catalogue-loading` (acted=no, writes nothing) to the first `loading` asks."""

    def __init__(self, *a, loading=1, during_retry=None, **kw):
        super().__init__(*a, **kw)
        self.loading, self.during_retry = loading, during_retry

    def ask(self, verb, *args):
        if self.loading > 0:
            self.loading -= 1
            self.asks.append((verb, args))
            slot, req = args
            adv = (f"write=yes verb=save reason=catalogue-loading acted=no slot={slot} "
                   f"label=x4live-{req} build=deadbeef")
            if self.during_retry:
                self.during_retry(self.save_dir)
            return _livepipe.Reply(seq=1, status="ERR", payload=adv + "\tstill loading")
        return super().ask(verb, *args)


def test_RED_a_catalogue_still_loading_is_asked_again_with_the_same_request(world, monkeypatch,
                                                                            capsys):
    save, clk = world
    engine = LoadingEngine(save, clk, loading=2)
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0, out + err
    assert len(engine.asks) == 3 and len({a[1] for a in engine.asks}) == 1, engine.asks


def test_TWIN_a_catalogue_that_never_loads_is_a_refusal_after_a_bounded_wait(world, monkeypatch,
                                                                             capsys):
    save, clk = world
    engine = LoadingEngine(save, clk, loading=10 ** 6)
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 1 and "reason=catalogue-loading" in out, out + err
    assert 2 <= len(engine.asks) <= 1 + _livesave.CATALOGUE_WAIT_S / _livesave.POLL_S + 1
    assert clk.t <= _livesave.CATALOGUE_WAIT_S + 1


def test_RED_the_slot_is_rechecked_before_every_retry(world, monkeypatch, capsys):
    """Another run's save lands while this one waits to ask again: refused, not asked."""
    save, clk = world
    engine = LoadingEngine(save, clk, loading=1)
    clk.hooks.append(lambda c: _gz(save / "save_004.xml.gz", "x4live-other"))
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and "occupied" in err and "Nothing more was sent" in err, out + err
    assert len(engine.asks) == 1


def test_TWIN_a_folder_change_during_a_loading_refusal_is_not_retried_but_reported(world,
                                                                                 monkeypatch,
                                                                                 capsys):
    """A refusal that acted on nothing must leave the folder as it was; if it did not, that
    is reported (exit 3), never papered over by asking again."""
    save, clk = world
    engine = LoadingEngine(save, clk, loading=1,
                           during_retry=lambda s: (s / "quicksave.xml.gz").write_bytes(b"x"))
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "changed: quicksave.xml.gz" in out, out + err
    assert len(engine.asks) == 1


def test_RED_a_lost_reply_on_the_retry_is_not_answered_by_the_earlier_refusal(world, monkeypatch,
                                                                             capsys):
    """The retry's reply is lost after SaveGame ran: the slot is read. The first ask's
    `catalogue-loading` refusal must not stand in for it (that read exit 1, 'refused')."""
    save, clk = world
    engine = LoadingEngine(save, clk, loading=1,
                           raises=_livepipe.LiveQueryUnavailable("rehearsed loss"))
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 0 and "reply was lost" in (out + err), out + err
    assert len(engine.asks) == 2


@pytest.mark.parametrize("old,new", [("acted=no", "acted=yes"),
                                     ("slot=save_004", "slot=save_005"),
                                     ("reason=catalogue-loading", "reason=not-saveable")])
def test_TWIN_each_clause_of_a_loading_refusal_is_needed_to_ask_again(old, new, world,
                                                                     monkeypatch, capsys):
    save, clk = world
    engine = LoadingEngine(save, clk, loading=1)
    real = engine.ask

    def altered(verb, *args):
        r = real(verb, *args)
        return _livepipe.Reply(seq=1, status=r.status, payload=r.payload.replace(old, new, 1))
    engine.ask = altered
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert len(engine.asks) == 1 and rc in (1, 3), (rc, out + err)


def test_TWIN_a_retry_is_not_made_for_another_requests_refusal(world, monkeypatch, capsys):
    """Only THIS request's trusted catalogue-loading refusal is asked again."""
    save, clk = world
    engine = LoadingEngine(save, clk, loading=1)
    real = engine.ask

    def other_label(verb, *args):
        r = real(verb, *args)
        return _livepipe.Reply(seq=1, status=r.status,
                               payload=r.payload.replace(f"label=x4live-{args[1]}",
                                                         "label=x4live-" + "f" * 32))
    engine.ask = other_label
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert rc == 3 and len(engine.asks) == 1, out + err


@pytest.mark.parametrize("name", ["ſteam_autocloud.vdf",      # LATIN SMALL LETTER LONG S
                                  "ﬆeam_autocloud.vdf",       # LATIN SMALL LIGATURE ST
                                  "ﬅeam_autocloud.vdf"])      # LATIN SMALL LIGATURE LONG S T
def test_RED_an_autocloud_look_alike_is_not_excepted(name):
    """casefold() maps each of these onto the ASCII name; only the real file is excepted."""
    assert not _livesave.is_autocloud(name)
    assert _livesave.compare({name: {"size": 1}}, {name: {"size": 2}}, None, []) == [
        f"changed: {name}"]


@pytest.mark.parametrize("name", ["steam_autocloud.vdf", "STEAM_AUTOCLOUD.VDF",
                                  "Steam_AutoCloud.Vdf"])
def test_TWIN_the_real_autocloud_name_in_any_ascii_case_is_excepted(name):
    assert _livesave.is_autocloud(name)
    notes = []
    assert _livesave.compare({name: {"size": 1}}, {name: {"size": 2}}, None, notes) == []
    assert len(notes) == 1


class OldHelper(Engine):
    """Helper 102: the dispatcher does not know `save` and runs nothing (live_query.lua's
    `unknown verb: ` reply, sent before any verb function is looked up)."""

    def ask(self, verb, *args):
        self.asks.append((verb, args))
        if self.side:
            self.side(self.save_dir)
        return _livepipe.Reply(seq=1, status="ERR", payload=f"unknown verb: {verb}")


def test_RED_a_helper_without_save_is_a_refusal_without_waiting(world, monkeypatch, capsys):
    save, clk = world
    engine = OldHelper(save, clk)
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 1, out + err
    assert clk.sleeps == 0, "nothing was acted, so there is no slot to wait for"
    assert "103" in out and "nothing in the save folder changed" in out
    assert len(engine.asks) == 1


def test_TWIN_a_helper_without_save_while_the_folder_changed_is_untrusted(world, monkeypatch,
                                                                          capsys):
    save, clk = world
    engine = OldHelper(save, clk, side=lambda s: (s / "quicksave.xml.gz").write_bytes(b"x"))
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 3 and "quicksave.xml.gz" in out, out + err


@pytest.mark.parametrize("status,payload", [("ERR", "unknown verb: pause"),
                                            ("ERR", "unknown verb: save extra"),
                                            ("ERR", "unknown verb: saves"),
                                            ("OK", "unknown verb: save")])
def test_TWIN_only_the_exact_unknown_verb_save_reply_counts(status, payload, world, monkeypatch,
                                                            capsys):
    save, clk = world
    engine = OldHelper(save, clk)
    engine.ask = lambda verb, *a: (engine.asks.append((verb, a)),
                                   _livepipe.Reply(seq=1, status=status, payload=payload))[1]
    _serve(monkeypatch, engine)
    rc, out, err = _run(capsys, "save_004", "--settle-timeout", "5")
    assert rc == 3 and clk.sleeps > 0, out + err


def test_RED_a_bad_slot_is_refused_before_the_save_folder_is_resolved(monkeypatch, capsys):
    """qa_sweep's offline cell relies on this order: the slot first, then the folder."""
    def unresolvable():
        raise AssertionError("the save folder was resolved for a refused slot")
    monkeypatch.setattr(C._paths, "savegames", unresolvable)
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_*")
    assert rc == 2 and "exactly one standard slot" in err, out + err


def test_TWIN_a_good_slot_does_resolve_the_save_folder(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(C._paths, "savegames", lambda: calls.append(1))
    _never_open(monkeypatch)
    rc, out, err = _run(capsys, "save_004")
    assert rc == 2 and calls == [1] and "not configured" in err, out + err

"""OFFLINE REHEARSAL of the whole `x4live save` chain, before any game launch.

    x4live main()  ->  _livesave.run  ->  _livepipe.encode_command  ->  the SHIPPED
    live_query.lua (real LuaJIT, the test_modlua_rearm harness)  ->  a fake engine  ->
    a scratch save folder  ->  _livepipe.decode_reply  ->  the slot read-back.

The fake engine takes its shapes from the game, never from our own code:

  * the save list: `GetSaveList(Helper.validSaveFilenames)` rows with filename, name,
    rawtime, isonline, error, invalidgameid, invalidversion, invalidpatches, modified,
    location, description, time, playername, money, version (gameoptions.lua:11608,
    3476, 5849-5874), built here FROM THE FOLDER, so the engine and the disk agree the way
    they do in game; an empty slot is a save_%03d absent from it (gameoptions.lua:11729);
  * `SaveGame(filename, name)` (gameoptions.lua:9296) does not write during the call: the
    file appears LATER, first truncated and then complete, as an asynchronous writer
    would (INFERRED: the timing of X4's own writer is unmeasured; the in-game run
    measures it);
  * the save file's header is the element shape READ from a real save (a scratch copy,
    2026-11-18): savegame/info/save@name+date, game@id="X4"+version+build+..., player,
    patches. Values are synthetic;
  * the folder holds what a real one holds: save_NNN.xml.gz, quicksave, autosaves and
    `steam_autocloud.vdf` (listing of a real save folder, names only).

Synthetic; this proves the chain is wired and refuses correctly, never that X4 behaves so.
"""
from __future__ import annotations

import contextlib
import gzip

import pytest

from x4validate import _livecli as C
from x4validate import _livepipe, _livesave
from test_modlua_rearm import _build, live, deliver, g

HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n<savegame>\n<info>\n'
          '<save name="{name}" date="1763478000"/>\n'
          '<game id="X4" version="900" build="600000" modified="0" time="1234.5" code="1" '
          'original="900" originalbuild="600000" start="custom_creative" seed="1" guid="g"/>\n'
          '<player name="Rehearsal" location="Sector" money="10000"/>\n'
          '<patches>\n<patch extension="ego_dlc_split" version="900" name="Split"/>\n'
          '</patches>\n</info>\n<universe>{body}</universe>\n</savegame>\n')


def save_bytes(name, body="x" * 20000):
    return gzip.compress(HEADER.format(name=name, body=body).encode("utf-8"))


def header_name(path):
    with gzip.open(path, "rb") as f:
        head = f.read(4096).decode("utf-8", "replace")
    i = head.find('<save name="') + len('<save name="')
    return head[i:head.find('"', i)]


ENGINE = r"""
function GetSaveList(filter) return py_save_list(filter) end
function IsSavingPossible(flag) return py_saving_possible(flag) end
function OnlineIsOnlineModeActive() return false end
function SaveGame(filename, name) py_save_game(filename, name) end
_G.fake_C.IsSaveListLoadingComplete = function() return py_list_complete() end
_G.fake_C.ReloadSaveList = function() py_reload() end
"""


class Game:
    """The fake engine + the real helper Lua, behind a Python pipe object."""

    path = "rehearsal-pipe"

    def __init__(self, save_dir, *, lose_reply=False, bystander=None, never_write=False):
        self.save_dir, self.lose_reply = save_dir, lose_reply
        self.bystander, self.never_write = bystander, never_write
        self.rt = live(rt=_build())
        self.pending, self.frames, self.seq, self.polls = [], [], 100, 0
        # The engine's save list is a CATALOGUE, not the folder: read once (at game start
        # here) and refreshed only by ReloadSaveList, which loads asynchronously -- vanilla
        # reloads, then waits for IsSaveListLoadingComplete before GetSaveList (gameoptions.lua
        # :13147-13157, :11605). So a file that lands after the last reload is NOT listed:
        # the stale-catalogue race is reproducible here. INFERRED: the load's duration in
        # game is unmeasured; one tick models "not immediately".
        self.catalogue, self.loading, self.reloads = self._scan(), False, 0
        lg = self.rt.globals()
        lg.py_save_list = self._save_list
        lg.py_saving_possible = lambda flag: flag is False
        lg.py_save_game = self._save_game
        lg.py_list_complete = lambda: not self.loading
        lg.py_reload = self._reload
        self.rt.execute(ENGINE)

    # ---- the engine, as gameoptions.lua sees it
    def _reload(self):
        self.reloads += 1
        self.loading = True

    def _save_list(self, filt):
        assert not self.loading, "GetSaveList read while the list was still loading"
        return self.rt.table_from([self.rt.table_from(r) for r in self.catalogue
                                   if filt[r["filename"]]])

    def _scan(self):
        rows = []
        for p in sorted(self.save_dir.glob("*.xml.gz")):
            stem = p.name[: -len(".xml.gz")]
            rows.append({
                "filename": stem, "name": header_name(p), "rawtime": int(p.stat().st_mtime),
                "isonline": False, "isonlinesavefilename": False, "error": False,
                "invalidgameid": False, "invalidversion": False, "invalidpatches": False,
                "modified": False, "location": "Sector", "description": "",
                "time": "1234.5", "playername": "Rehearsal", "money": 10000, "version": "900"})
        return rows

    def _save_game(self, filename, name):
        self.pending.append((filename, name))

    def tick(self):
        """One poll interval of game time: the asynchronous writer makes progress, and a
        save-list reload in flight completes (reading the folder as it is NOW)."""
        self.polls += 1
        if self.loading:
            self.catalogue, self.loading = self._scan(), False
        if self.bystander and self.polls == 2:
            self.bystander(self.save_dir)
        if self.never_write:
            return
        for filename, name in self.pending:
            data = save_bytes(name)
            target = self.save_dir / f"{filename}.xml.gz"
            if self.polls == 2:
                target.write_bytes(data[: len(data) // 3])
            elif self.polls == 9:
                target.write_bytes(data)

    # ---- the pipe, as _livepipe would carry it
    def ask(self, verb, *args):
        self.seq += 1
        msg = _livepipe.encode_command(self.seq, verb, tuple(args))
        assert len(msg.encode()) <= _livepipe.MAX_REQUEST_BYTES
        deliver(self.rt, msg)
        frame = g(self.rt, "writes")[len(g(self.rt, "writes"))]["msg"]
        self.frames.append((verb, args, frame))
        # A LOST reply is rehearsed for the ask that reached SaveGame: a lost refusal
        # (catalogue-loading) would leave nothing to find, which the next test covers.
        if self.lose_reply and "reason=requested" in frame:
            raise _livepipe.LiveQueryUnavailable("no reply within 10s (rehearsed loss)")
        return _livepipe.decode_reply(frame, self.seq)


@pytest.fixture
def folder(tmp_path, monkeypatch):
    save = tmp_path / "save"
    save.mkdir()
    for i in (1, 2, 3):
        (save / f"save_{i:03}.xml.gz").write_bytes(save_bytes(f"#{i:03}"))
    (save / "quicksave.xml.gz").write_bytes(save_bytes("#q"))
    for i in (1, 2, 3):
        (save / f"autosave_{i:02}.xml.gz").write_bytes(save_bytes(f"#a{i}"))
    (save / "steam_autocloud.vdf").write_bytes(b'"steam_autocloud.vdf"\n{\n}\n' + b" " * 23)
    monkeypatch.setattr(C._paths, "savegames", lambda: save)
    return save


def _connect(monkeypatch, game):
    clock = {"t": 0.0}

    def sleep(dt):
        clock["t"] += dt
        game.tick()
    monkeypatch.setattr(_livesave, "_clock", lambda: clock["t"])
    monkeypatch.setattr(_livesave, "_sleep", sleep)

    @contextlib.contextmanager
    def opened(pipe, timeout):
        yield game
    monkeypatch.setattr(C, "_live_open", opened)


def _x4live(capsys, *argv):
    rc = C.main(list(argv))
    out, err = capsys.readouterr()
    print(f"\n$ x4live {' '.join(argv)}  -> rc {rc}\n--- stdout\n{out}--- stderr\n{err}")
    return rc, out, err


def _bytes(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir()}


def test_rehearsal_empty_slot_success(folder, monkeypatch, capsys):
    before = _bytes(folder)
    game = Game(folder)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_004")
    assert rc == 0, out + err
    # The helper reloads its save list on the first request and refuses while it loads;
    # the host asks again with the SAME request, never a new one.
    assert [f[0] for f in game.frames] == ["save"] * len(game.frames) and game.reloads >= 1
    assert len({f[1][1] for f in game.frames}) == 1
    assert sum("reason=requested" in f[2] for f in game.frames) == 1
    after = _bytes(folder)
    assert set(after) - set(before) == {"save_004.xml.gz"}
    assert all(after[k] == v for k, v in before.items())
    assert header_name(folder / "save_004.xml.gz") == "x4live-" + game.frames[0][1][1]
    assert game.polls > 9, "the host must have waited out the truncated stage"


def test_rehearsal_then_the_same_slot_is_refused_as_occupied(folder, monkeypatch, capsys):
    game = Game(folder)
    _connect(monkeypatch, game)
    assert _x4live(capsys, "save", "save_004")[0] == 0
    writes = len(g(game.rt, "writes"))
    rc, out, err = _x4live(capsys, "save", "save_004")
    assert rc == 2 and "occupied" in err
    assert len(g(game.rt, "writes")) == writes, "an occupied slot must never reach the game"


def test_rehearsal_occupied_slot_refused_before_sending(folder, monkeypatch, capsys):
    before = _bytes(folder)
    game = Game(folder)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_002")
    assert rc == 2 and "occupied" in err
    assert game.frames == [] and _bytes(folder) == before


def test_rehearsal_engine_side_occupancy_is_a_second_wall(folder, monkeypatch, capsys):
    """Bypass the host's check (another program on the pipe): the helper still refuses."""
    game = Game(folder)
    r = game.ask("save", "save_001", "0" * 32)
    if "reason=catalogue-loading" in r.fields[0]:   # the first request reloads the list
        game.tick()
        r = game.ask("save", "save_001", "0" * 32)
    assert r.status == "ERR" and "reason=occupied" in r.fields[0] and game.pending == []


def test_rehearsal_lost_reply_resolved_by_reading_the_slot(folder, monkeypatch, capsys):
    game = Game(folder, lose_reply=True)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_005")
    assert rc == 0, out + err
    assert "reason=requested" in game.frames[-1][2], "a lost reply must never be answered by resending"
    assert sum("reason=requested" in f[2] for f in game.frames) == 1
    assert "reply was lost" in (out + err)


def test_rehearsal_lost_reply_and_no_save_is_unverified(folder, monkeypatch, capsys):
    game = Game(folder, lose_reply=True, never_write=True)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_005", "--settle-timeout", "10")
    assert rc == 3 and "reason=requested" in game.frames[-1][2]
    assert "Do not resend" in out


def test_rehearsal_bystander_change_exits_3_and_is_named(folder, monkeypatch, capsys):
    def autosave(save):
        (save / "autosave_02.xml.gz").write_bytes(save_bytes("#a2-new"))
    game = Game(folder, bystander=autosave)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_006")
    assert rc == 3, out + err
    assert "changed: autosave_02.xml.gz" in out


def test_rehearsal_steam_autocloud_change_is_reported_not_failed(folder, monkeypatch, capsys):
    """steam_autocloud.vdf is a real member of a real save folder that Steam rewrites;
    user decision 2026-11-18: reported, not a failure."""
    def steam(save):
        (save / "steam_autocloud.vdf").write_bytes(b'"steam_autocloud.vdf"\n{\n "x" "1"\n}\n')
    game = Game(folder, bystander=steam)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_006")
    assert rc == 0, out + err
    assert "steam_autocloud.vdf changed (reported" in out


# ---- the never-overwrite races (4.3 release review D1). The catalogue above is what makes
# them reproducible: a list read from the folder on every call could never be stale.


def test_RED_rehearsal_two_requests_for_one_slot_before_its_file_lands(folder, monkeypatch, capsys):
    """Two `x4live save save_004` runs racing: both host checks see an empty slot (the
    first save is not written yet), so the helper is the only wall left."""
    game = Game(folder)
    for _ in range(3):   # bounded: a helper that never finishes loading must fail, not hang
        first = game.ask("save", "save_004", "a" * 32)
        if "reason=catalogue-loading" not in first.fields[0]:
            break
        game.tick()
    assert first.status == "OK", first.payload
    assert not (folder / "save_004.xml.gz").exists(), "the race needs the file not yet written"
    for _ in range(3):
        second = game.ask("save", "save_004", "b" * 32)
        if "reason=catalogue-loading" not in second.fields[0]:
            break
        game.tick()
    assert second.status == "ERR" and "reason=occupied" in second.fields[0], second.payload
    assert [p[0] for p in game.pending] == ["save_004"], "a second SaveGame overwrites the first"


def test_RED_rehearsal_a_save_made_after_the_list_was_read_is_never_overwritten(folder, monkeypatch,
                                                                              capsys):
    """A save that lands after the engine last read its list (the player saving in game,
    another program) is invisible to a stale catalogue; the helper must reload first."""
    game = Game(folder)
    (folder / "save_007.xml.gz").write_bytes(save_bytes("#007 made in game"))
    for _ in range(3):
        r = game.ask("save", "save_007", "c" * 32)
        if "reason=catalogue-loading" not in r.fields[0]:
            break
        game.tick()
    assert r.status == "ERR" and "reason=occupied" in r.fields[0], r.payload
    assert game.pending == [] and game.reloads >= 1
    assert header_name(folder / "save_007.xml.gz") == "#007 made in game"


def test_TWIN_rehearsal_an_empty_slot_is_saved_after_the_reload_completes(folder, monkeypatch, capsys):
    """Through the whole chain: the helper reloads, refuses `catalogue-loading` while the
    list loads, the host asks again with the same request, and the save lands."""
    before = _bytes(folder)
    game = Game(folder)
    _connect(monkeypatch, game)
    rc, out, err = _x4live(capsys, "save", "save_008")
    assert rc == 0, out + err
    assert game.reloads >= 1
    labels = {f[1][1] for f in game.frames}
    assert len(labels) == 1 and len(game.frames) >= 2, game.frames
    assert "reason=catalogue-loading" in game.frames[0][2]
    after = _bytes(folder)
    assert set(after) - set(before) == {"save_008.xml.gz"}
    assert all(after[k] == v for k, v in before.items())

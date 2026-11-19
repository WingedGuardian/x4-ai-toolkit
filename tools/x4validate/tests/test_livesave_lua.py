"""The helper's `save` write verb, run in a real LuaJIT against a fake engine.

The fake engine's shapes are the game's own, READ from the vanilla UI Lua:

  * `GetSaveList(Helper.validSaveFilenames)` returns rows with `filename`, `name`,
    `rawtime`, `isonline`, `error`, `invalidgameid`, `invalidversion`, `invalidpatches`,
    `modified`, `location`, `description`, `time`, `playername`, `money`, `version`
    (ego_gameoptions/gameoptions.lua:11608, 3476, 5849-5874, 6079-6119);
    the filter is the table `helper.lua:881-889` builds;
  * an EMPTY manual slot is a save_%03d filename ABSENT from that list
    (gameoptions.lua:11729-11735);
  * `SaveGame(filename, name)` is called bare with two strings (gameoptions.lua:9296, 9310);
  * `IsSavingPossible(false)` gates saving (gameoptions.lua:6241, 1277);
  * `C.IsSaveListLoadingComplete()` must be true before the list is read (gameoptions.lua:
    11605, declared at :271); `OnlineIsOnlineModeActive()` is a bare global (:11197).

Every refusal starts from the passing baseline and flips ONE input, so the baseline is the
falsification twin of each clause. Synthetic engine; never proof of in-game behaviour.
"""
from __future__ import annotations

import pytest

from test_modlua_rearm import _build, live, ask, g

REQ = "0123456789abcdef0123456789abcdef"
LABEL = "x4live-" + REQ

ENGINE = r"""
local function row(filename, name)
    return {filename=filename, name=name, rawtime=1721947652, isonline=false,
            isonlinesavefilename=false, error=false, invalidgameid=false,
            invalidversion=false, invalidpatches=false, modified=false,
            location="Sector", description="", time="499.905", playername="p",
            money=10000, version="900"}
end
_G.save_rows = {row("save_001", "#001"), row("quicksave", "#q"), row("autosave_01", "#a")}
_G.savelist_complete = true
_G.saving_possible = true
_G.online = false
_G.save_calls = {}
_G.savelist_filters = {}
_G.saving_possible_args = {}
function GetSaveList(filter)
    _G.savelist_filters[#_G.savelist_filters + 1] = filter
    if _G.savelist_raises then error("GetSaveList failed") end
    if _G.savelist_nontable then return 7 end
    if _G.savelist_raw then return _G.savelist_raw end
    local out = {}
    for _, r in ipairs(_G.save_rows) do
        if filter[r.filename] then out[#out + 1] = r end
    end
    return out
end
function IsSavingPossible(flag)
    _G.saving_possible_args[#_G.saving_possible_args + 1] = flag
    return _G.saving_possible
end
function OnlineIsOnlineModeActive() return _G.online end
function SaveGame(...)
    _G.save_calls[#_G.save_calls + 1] = {...}
    _G.save_nargs = select("#", ...)
    if _G.savegame_raises then error("engine refused to save") end
end
_G.fake_C.IsSaveListLoadingComplete = function() return _G.savelist_complete end
"""


def _rt(pre="", code=""):
    rt = live(rt=_build(pre=pre))
    rt.execute(ENGINE + code)
    return rt


def _adv(r):
    first = r.fields[0]
    assert first.startswith("write=yes "), r.payload
    return dict(t.split("=", 1) for t in first.split(" ") if "=" in t)


def _calls(rt):
    return len(g(rt, "save_calls"))


def test_an_empty_slot_is_saved_with_this_requests_label():
    rt = _rt()
    r = ask(rt, 1, "save", "save_004", REQ)
    a = _adv(r)
    assert r.status == "OK", r.payload
    assert (a["verb"], a["reason"], a["acted"], a["slot"], a["label"]) == (
        "save", "requested", "yes", "save_004", LABEL)
    assert _calls(rt) == 1
    call = g(rt, "save_calls")[1]
    assert (call[1], call[2], g(rt, "save_nargs")) == ("save_004", LABEL, 2)
    # IsSavingPossible is asked exactly as vanilla asks it before saving.
    assert list(g(rt, "saving_possible_args").values()) == [False]


def test_the_catalogue_filter_is_vanillas_valid_save_filenames():
    rt = _rt()
    ask(rt, 1, "save", "save_004", REQ)
    f = g(rt, "savelist_filters")[1]
    names = sorted(k for k, v in f.items() if v is True)
    want = sorted(["online_save", "quicksave", *(f"autosave_{i:02}" for i in range(1, 4)),
                   *(f"save_{i:03}" for i in range(1, 11))])
    assert names == want


def test_every_attempt_is_logged_to_debug_txt():
    rt = _rt()
    ask(rt, 1, "save", "save_004", REQ)
    log = list(g(rt, "log").values())
    assert any("X4TOOLKIT_LIVE WRITE save seq=1" in s for s in log), log
    assert any("WRITE save result: reason=requested acted=yes slot=save_004" in s
               for s in log), log


@pytest.mark.parametrize("args", [(), ("save_004",), ("save_004", REQ, "extra")])
def test_the_argument_count_is_exactly_slot_and_request(args):
    rt = _rt()
    r = ask(rt, 1, "save", *args)
    assert r.status == "ERR" and _adv(r)["reason"] == "args" and _adv(r)["acted"] == "no"
    assert _calls(rt) == 0


@pytest.mark.parametrize("slot", ["save_000", "save_011", "save_*", "save_00.", "SAVE_004",
                                  "quicksave", "autosave_01", "online_save", "save_0040",
                                  "save_004.xml.gz"])
def test_only_a_standard_slot_is_accepted(slot):
    rt = _rt()
    r = ask(rt, 1, "save", slot, REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "slot", r.payload
    assert _calls(rt) == 0


@pytest.mark.parametrize("req", ["", "0123", REQ.upper(), REQ + "0", "g" * 32])
def test_the_request_id_is_32_lowercase_hex(req):
    rt = _rt()
    r = ask(rt, 1, "save", "save_004", req) if req else ask(rt, 1, "save", "save_004", "-")
    assert r.status == "ERR" and _adv(r)["reason"] == "request", r.payload
    assert _calls(rt) == 0


@pytest.mark.parametrize("name", ["GetSaveList", "IsSavingPossible", "SaveGame",
                                  "OnlineIsOnlineModeActive"])
def test_a_missing_engine_global_refuses(name):
    rt = _rt(code=f"{name} = nil")
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "no-engine-call", r.payload
    assert name in r.payload and _calls(rt) == 0


def test_a_failed_catalogue_declaration_refuses_and_leaves_reads_working():
    rt = _rt(pre='_G.cdef_raises_on = "IsSaveListLoadingComplete"')
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "unreadable", r.payload
    assert _calls(rt) == 0
    assert ask(rt, 2, "ping").status == "OK"


@pytest.mark.parametrize("value,reason", [("false", "catalogue-loading"), ("nil", "unreadable"),
                                          ("1", "unreadable")])
def test_an_incomplete_or_unreadable_catalogue_refuses(value, reason):
    rt = _rt(code=f"_G.fake_C.IsSaveListLoadingComplete = function() return {value} end")
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == reason, r.payload
    assert _calls(rt) == 0


@pytest.mark.parametrize("value,reason", [("true", "online"), ("nil", "unreadable")])
def test_online_or_unknown_mode_refuses(value, reason):
    rt = _rt(code=f"_G.online = {value}")
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == reason, r.payload
    assert _calls(rt) == 0


@pytest.mark.parametrize("value,reason", [("false", "not-saveable"), ("nil", "unreadable")])
def test_a_game_that_cannot_save_now_refuses(value, reason):
    rt = _rt(code=f"_G.saving_possible = {value}")
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == reason, r.payload
    assert _calls(rt) == 0


def test_a_slot_the_engine_lists_is_occupied():
    rt = _rt()
    r = ask(rt, 1, "save", "save_001", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "occupied", r.payload
    assert _calls(rt) == 0


def test_a_label_already_in_the_list_refuses():
    rt = _rt(code='_G.save_rows[#_G.save_rows + 1] = {filename="save_009", name="%s", '
                  'rawtime=1}' % LABEL)
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "label-exists", r.payload
    assert _calls(rt) == 0


@pytest.mark.parametrize("code", ["_G.savelist_raises = true", "_G.savelist_nontable = true",
                                  '_G.savelist_raw = {{name = "#001"}}',
                                  '_G.savelist_raw = {"save_001"}'])
def test_an_unreadable_save_list_refuses(code):
    rt = _rt(code=code)
    r = ask(rt, 1, "save", "save_004", REQ)
    assert r.status == "ERR" and _adv(r)["reason"] == "unreadable", r.payload
    assert _calls(rt) == 0


def test_a_save_call_that_raises_reports_acted_yes():
    rt = _rt(code="_G.savegame_raises = true")
    r = ask(rt, 1, "save", "save_004", REQ)
    a = _adv(r)
    assert r.status == "ERR" and a["reason"] == "raised" and a["acted"] == "yes", r.payload
    assert _calls(rt) == 1


def test_save_lives_only_in_WRITE_VERBS():
    rt = _build(extra="_G.__probe = {r = verbs.save ~= nil, w = WRITE_VERBS.save ~= nil}")
    p = g(rt, "__probe")
    assert (p["r"], p["w"]) == (False, True)

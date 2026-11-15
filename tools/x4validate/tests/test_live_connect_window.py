"""The client half of the helper's idle back-off, and the helper-build mismatch warning.

WHY THIS EXISTS (2026-11-14). The game-side helper no longer re-arms its pipe read every
2 s while nobody is connected: idle re-arms back off 2 -> 4 -> 8 -> 10 s
(`REARM_IDLE_MAX` in the shipped live_query.lua). WE are the pipe server and the game is
the client, so a fresh x4live call is only served when the helper's NEXT arm comes round
-- up to the cap away. The CLI waits for that connection in `LivePipe.wait_for_game`, and
its old window was the per-reply `--timeout` (10 s by default): a cap that equals the
window is a coin-flip timeout. So the connect window has a floor above the cap, pinned
here to the number in the lua, and the per-reply timeout is left alone.

Second half: x4live warns on stderr (never refuses) when the helper the game reports
having LOADED is not the build this toolkit ships -- the "deployed is not loaded" class
the BUILD fingerprint exists for.
"""
from __future__ import annotations

import io
import pathlib
import re
import time
from types import SimpleNamespace

import pytest

from x4validate import _livecli
from x4validate import _livepipe as lp

_LUA = (pathlib.Path(__file__).resolve().parents[3]
        / "mods" / "x4_toolkit_helper" / "ui" / "live_query.lua")


def _lua_text():
    if not _LUA.is_file():
        pytest.skip("no shipped helper lua in this tree")
    return _LUA.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the connect window
# --------------------------------------------------------------------------- #

def test_the_python_cap_IS_the_lua_cap():
    """One number in two languages: if the lua cap grows, the window must follow."""
    m = re.search(r"^local REARM_IDLE_MAX = ([0-9.]+)$", _lua_text(), re.M)
    assert m, "REARM_IDLE_MAX not found in the shipped lua"
    assert float(m.group(1)) == lp.HELPER_IDLE_REARM_MAX_S


def test_the_connect_window_floor_clears_the_cap_with_margin():
    """At least one fast cycle (2 s) of margin over the cap, for frame jitter and the
    arm's own synchronous poll."""
    assert lp.CONNECT_WINDOW_MIN_S >= lp.HELPER_IDLE_REARM_MAX_S + 2.0


class _RecordingPipe:
    made = []

    def __init__(self, name=None, timeout=10.0, connect_timeout=None):
        self.name, self.timeout, self.connect_timeout = name, timeout, connect_timeout
        self.path = r"\\.\pipe\test"
        _RecordingPipe.made.append(self)

    def open(self):
        pass

    def wait_for_game(self):
        pass

    def close(self):
        pass


def _open_with(monkeypatch, timeout, log=None):
    _RecordingPipe.made = []
    monkeypatch.setattr(lp, "LivePipe", _RecordingPipe)
    monkeypatch.setattr(lp._paths, "debug_log", lambda: log)
    err = io.StringIO()
    monkeypatch.setattr(_livecli.sys, "stderr", err)
    with _livecli._live_open(None, timeout) as p:
        assert p is _RecordingPipe.made[0], "the CLI must still be handed its pipe"
    return _RecordingPipe.made[0], err.getvalue()


def test_a_SHORT_timeout_still_waits_the_window_for_the_CONNECT(monkeypatch):
    p, _ = _open_with(monkeypatch, 3.0)
    assert p.connect_timeout == lp.CONNECT_WINDOW_MIN_S
    assert p.timeout == 3.0, "the per-REPLY timeout must not be stretched"


def test_a_LONG_timeout_is_kept_for_the_connect(monkeypatch):
    """Twin: the floor only ever raises the window, never lowers what was asked for."""
    p, _ = _open_with(monkeypatch, 40.0)
    assert p.connect_timeout == 40.0


def _listening_win32(monkeypatch):
    calls = []

    def listening(*args):
        calls.append(time.monotonic())
        error = OSError("still listening")
        error.winerror = 536
        raise error

    monkeypatch.setattr(lp, "_win32", lambda: (
        SimpleNamespace(ConnectNamedPipe=listening), None,
        SimpleNamespace(ERROR_PIPE_CONNECTED=535, ERROR_PIPE_LISTENING=536, ERROR_NO_DATA=232)))
    monkeypatch.setattr(lp, "game_is_running", lambda: True)
    monkeypatch.setattr(lp, "game_is_minimized", lambda: False)
    monkeypatch.setattr(lp, "helper_deployment", lambda: (True, None))
    monkeypatch.setattr(lp, "helper_loaded_this_session", lambda: True)
    monkeypatch.setattr(lp, "helper_ui_safe_mode_at_load", lambda: None)
    return calls


def test_wait_for_game_waits_the_CONNECT_window_not_the_reply_timeout(monkeypatch):
    calls = _listening_win32(monkeypatch)
    pipe = lp.LivePipe(name="test", timeout=0, connect_timeout=0.3)
    t0 = time.monotonic()
    with pytest.raises(lp.LiveQueryUnavailable) as ei:
        pipe.wait_for_game()
    assert time.monotonic() - t0 >= 0.3
    assert len(calls) > 1, "it must keep polling for the connection, not try once"
    assert "within 0s" in str(ei.value)  # 0.3 rendered with %.0f: the window waited


def test_the_refusal_names_the_window_actually_waited(monkeypatch):
    """A refusal that said 'within 7s' after waiting 0 s would misdescribe the wait."""
    _listening_win32(monkeypatch)
    pipe = lp.LivePipe(name="test", timeout=7, connect_timeout=0)
    with pytest.raises(lp.LiveQueryUnavailable) as ei:
        pipe.wait_for_game()
    assert "within 0s" in str(ei.value) and "within 7s" not in str(ei.value)


def test_WITHOUT_a_connect_timeout_the_window_is_the_timeout(monkeypatch):
    """Twin, and the compatibility contract for direct LivePipe users."""
    _listening_win32(monkeypatch)
    pipe = lp.LivePipe(name="test", timeout=0)
    assert pipe.connect_timeout == 0
    with pytest.raises(lp.LiveQueryUnavailable):
        pipe.wait_for_game()


# --------------------------------------------------------------------------- #
# the helper-build mismatch warning
# --------------------------------------------------------------------------- #

def test_the_expected_build_is_the_shipped_lua_stamp():
    m = re.search(r'^local BUILD = "([0-9a-f]{8})"$', _lua_text(), re.M)
    assert m and lp.expected_helper_build() == m.group(1)


def test_no_warning_when_the_builds_AGREE():
    assert lp.helper_build_warning("abcd1234", "abcd1234", "probe") is None


def test_a_warning_NAMING_both_builds_when_they_DIFFER():
    w = lp.helper_build_warning("abcd1234", "deadbeef", "the load marker")
    assert w and "abcd1234" in w and "deadbeef" in w and "the load marker" in w


@pytest.mark.parametrize("expected,reported", [(None, "deadbeef"), ("abcd1234", None)])
def test_no_warning_when_either_side_is_UNKNOWN(expected, reported):
    """Unknown is not a mismatch: nothing was compared, so nothing is claimed."""
    assert lp.helper_build_warning(expected, reported, "probe") is None


def _marker_log(tmp_path, build):
    p = tmp_path / "debug.txt"
    p.write_text("[=ERROR=] 0.03 X4TOOLKIT_LIVE loaded: proto=1 pipe=x4live "
                 f"build={build} ui_safe_mode=false\n", encoding="utf-8")
    return p


def test_the_loaded_build_is_read_from_the_LATEST_marker(tmp_path, monkeypatch):
    p = tmp_path / "debug.txt"
    p.write_text("X4TOOLKIT_LIVE loaded: proto=1 build=11111111\n"
                 "X4TOOLKIT_LIVE loaded: proto=1 build=22222222\n", encoding="utf-8")
    monkeypatch.setattr(lp._paths, "debug_log", lambda: p)
    assert lp.helper_loaded_build() == "22222222"


def test_live_open_WARNS_on_stderr_when_the_loaded_build_differs(tmp_path, monkeypatch):
    _, err = _open_with(monkeypatch, 3.0, _marker_log(tmp_path, "deadbeef"))
    assert "deadbeef" in err and lp.expected_helper_build() in err, err
    assert "WARNING" in err


def test_live_open_is_SILENT_when_the_loaded_build_matches(tmp_path, monkeypatch):
    """Twin: the warning must come from the mismatch, not from opening the pipe."""
    _, err = _open_with(monkeypatch, 3.0, _marker_log(tmp_path, lp.expected_helper_build()))
    assert err == ""


def test_a_probe_reply_with_a_different_build_WARNS(monkeypatch):
    """The probe is the live helper's own answer, so it is compared too."""
    class _Ctx:
        path = r"\\.\pipe\test"

        def ask(self, verb, *args):
            return SimpleNamespace(status="OK", fields=["ping=function", "build=deadbeef"],
                                   payload="x")

    import contextlib

    @contextlib.contextmanager
    def fake_open(pipe, timeout):
        yield _Ctx()

    monkeypatch.setattr(_livecli, "_live_open", fake_open)
    err = io.StringIO()
    monkeypatch.setattr(_livecli.sys, "stderr", err)
    rc = _livecli.cmd_query("probe", [], None, 3.0, out=io.StringIO())
    assert rc == 0, "a mismatch warns; it never refuses"
    assert "deadbeef" in err.getvalue()


def test_a_probe_reply_with_the_SAME_build_is_silent(monkeypatch):
    class _Ctx:
        path = r"\\.\pipe\test"

        def ask(self, verb, *args):
            return SimpleNamespace(status="OK",
                                   fields=["build=" + lp.expected_helper_build()],
                                   payload="x")

    import contextlib

    @contextlib.contextmanager
    def fake_open(pipe, timeout):
        yield _Ctx()

    monkeypatch.setattr(_livecli, "_live_open", fake_open)
    err = io.StringIO()
    monkeypatch.setattr(_livecli.sys, "stderr", err)
    assert _livecli.cmd_query("probe", [], None, 3.0, out=io.StringIO()) == 0
    assert err.getvalue() == ""

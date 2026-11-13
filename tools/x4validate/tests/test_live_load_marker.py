"""Did the helper mod LOAD this session? -- read from a LIVE debug.txt, or refused.

WHY THIS EXISTS. When X4 is running but nothing connects, the refusal could not say WHICH of
two very different things was wrong: the mod is not installed/enabled, or it is loaded and the
frame loop is not executing (minimized). The fix for one is not the fix for the other, and the
hint told the user to go grep debug.txt by hand. This reads that marker for them.

THE SOUNDNESS RULE, and the reason most of these tests are about refusing: debug.txt OUTLIVES
the run that wrote it. A marker in a stale log proves the mod loaded in SOME session, not THIS
one -- so concluding "loaded" from it would be exactly the "a set difference is a QUESTION"
error. Staleness therefore DOMINATES content in both directions: a stale log answers None
whether or not it carries the marker.

Like `game_is_running`, this only ever shapes a MESSAGE. It never decides an outcome, so a
machine whose log is unreadable loses specificity and never gets a different verdict.
"""
from __future__ import annotations

import time

import pytest

from x4validate import _livepipe as lp

#: The GENERIC suffix, never the deployment-specific prefix -- F73: spelling the full marker in
#: a shipped file put a personal identifier into it. The mod's own line ends with this.
MARKER = "_LIVE loaded"


def _log(tmp_path, body: str, age_s: float = 0.0):
    p = tmp_path / "debug.txt"
    p.write_text(body, encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        import os
        os.utime(p, (old, old))
    return p


def _with_log(monkeypatch, path):
    monkeypatch.setattr(lp._paths, "debug_log", lambda: path)


def test_a_LIVE_log_carrying_the_marker_reports_LOADED(tmp_path, monkeypatch):
    _with_log(monkeypatch, _log(tmp_path, f"[=ERROR=] 0.03 X4TOOLKIT{MARKER}: proto=1\n"))
    assert lp.helper_loaded_this_session() is True


def test_a_LIVE_log_WITHOUT_the_marker_reports_NOT_LOADED(tmp_path, monkeypatch):
    """The twin. Without this, a function that always answered None would pass the test
    above's negation and still be useless."""
    _with_log(monkeypatch, _log(tmp_path, "[=ERROR=] 0.03 something else entirely\n"))
    assert lp.helper_loaded_this_session() is False


def test_a_STALE_log_REFUSES_even_though_it_carries_the_marker(tmp_path, monkeypatch):
    """THE soundness case. debug.txt outlives its run; a marker in a stale file proves the mod
    loaded in SOME session, not this one. Answering True here would assert a thing not measured."""
    _with_log(monkeypatch, _log(tmp_path, f"X4TOOLKIT{MARKER}\n",
                                age_s=lp.LOG_LIVE_WINDOW_S + 60))
    assert lp.helper_loaded_this_session() is None


def test_a_STALE_log_WITHOUT_the_marker_ALSO_refuses(tmp_path, monkeypatch):
    """Staleness dominates content in BOTH directions -- otherwise 'not loaded' could be
    asserted from a log written before the mod was ever installed."""
    _with_log(monkeypatch, _log(tmp_path, "nothing here\n", age_s=lp.LOG_LIVE_WINDOW_S + 60))
    assert lp.helper_loaded_this_session() is None


def test_an_UNRESOLVED_log_refuses(monkeypatch):
    _with_log(monkeypatch, None)
    assert lp.helper_loaded_this_session() is None


def test_an_UNREADABLE_log_refuses(tmp_path, monkeypatch):
    _with_log(monkeypatch, tmp_path / "does-not-exist.txt")
    assert lp.helper_loaded_this_session() is None


# --- is the extension one the ENGINE would load? ---------------------------- #
#
# The marker alone cannot separate "not installed" from "no save loaded yet", because the mod
# initialises on GAME LOAD -- at the main menu its marker is legitimately absent. This reads
# the install directly instead of inferring it from a log.

def _with_active(monkeypatch, ids, scopes=None):
    """Stub `_registry.mods`, recording the scope it was asked for."""
    from x4validate import _registry

    def fake(scope, *a, **kw):
        if scopes is not None:
            scopes.append(scope)
        return [{"id": i, "enabled": True} for i in (ids if scope == "active" else [*ids, "off"])]

    monkeypatch.setattr(_registry, "mods", fake)


def test_helper_is_deployed_TRUE_when_the_id_is_in_the_ACTIVE_set(monkeypatch):
    _with_active(monkeypatch, ["ws_2042901274", lp.HELPER_EXTENSION_ID])
    assert lp.helper_is_deployed() is True


def test_helper_is_deployed_FALSE_when_the_id_is_ABSENT(monkeypatch):
    """The twin. Without it, a function that always answered True would pass the test above."""
    _with_active(monkeypatch, ["ws_2042901274", "something_else"])
    assert lp.helper_is_deployed() is False


def test_helper_is_deployed_asks_for_ACTIVE_not_INSTALLED(monkeypatch):
    """CLAUDE.md #24. A folder on disk the profile has switched off is NOT something the engine
    loads; calling it deployed is the false pass that made the scope argument mandatory. The
    stub answers a DIFFERENT set per scope, so an `installed` caller would still read True."""
    scopes: list[str] = []
    other = {"id": "ws_2042901274", "enabled": True}

    def per_scope(scope, *a, **kw):
        scopes.append(scope)
        # The helper is on disk but profile-disabled: present under "installed", absent under
        # "active". Both sets are NON-EMPTY on purpose -- an empty "active" now answers None
        # (see the empty-set test below), which would make this pass for the wrong reason.
        return [other] if scope == "active" else [other,
                                                 {"id": lp.HELPER_EXTENSION_ID, "enabled": False}]

    monkeypatch.setattr("x4validate._registry.mods", per_scope)
    assert lp.helper_is_deployed() is False, "an 'installed' caller would have read True here"
    assert scopes == ["active"], f"scope must be 'active', got {scopes}"


def test_an_UNCONFIGURED_toolkit_REFUSES_rather_than_claiming_NOT_deployed(monkeypatch):
    """"I was never told where extensions\\ is" is a fact about our configuration, not about
    the user's install. Answering False would put the second claim in the first one's grammar."""
    from x4validate import _paths

    def boom(scope, *a, **kw):
        raise _paths.Unconfigured("extensions root not configured")

    monkeypatch.setattr("x4validate._registry.mods", boom)
    assert lp.helper_is_deployed() is None


def test_an_UNREADABLE_extensions_root_REFUSES(monkeypatch):
    def boom(scope, *a, **kw):
        raise OSError("extensions root is gone")

    monkeypatch.setattr("x4validate._registry.mods", boom)
    assert lp.helper_is_deployed() is None


def test_an_EMPTY_mod_set_REFUSES_instead_of_reporting_NOT_deployed(monkeypatch):
    """★ A DEFECT IN THE FIRST VERSION OF THIS FUNCTION, found 2026-09-20 by running it against
    a deliberately-wrong config rather than a stub.

    A CONFIGURED BUT NONEXISTENT `extensions\\` root raises nothing -- `_paths` resolves it
    happily and `scan_installed` walks a directory that is not there, yielding 0 mods and 0
    drops, in silence. `any()` over that answered **False**, so the refusal asserted "the helper
    extension is NOT in the set the engine would load" when the truth was "I could not look".
    MEASURED: 0 active mods, 0 dropped, no exception. The `except Unconfigured/OSError` guard
    never fired, and every test here stubbed `mods` to RAISE, so none of them could see it.

    An EMPTY population cannot support a negative (CLAUDE.md #9). The cost of None is
    specificity on a genuinely vanilla install; the cost of False is a confident wrong claim
    about someone's install. Refuse."""
    monkeypatch.setattr("x4validate._registry.mods", lambda scope, *a, **kw: [])
    assert lp.helper_is_deployed() is None


def test_a_NON_EMPTY_mod_set_WITHOUT_the_helper_still_reports_FALSE(monkeypatch):
    """The twin, and it is the whole point of the clause above: refusing on an empty set must
    NOT become refusing whenever the helper is absent. A populated mod set that does not
    contain it IS evidence, and that user still needs to be told to check their install."""
    _with_active(monkeypatch, ["ws_2042901274", "something_else"])
    assert lp.helper_is_deployed() is False


# --- is the window MINIMIZED? ------------------------------------------------ #
#
# VERIFIED IN GAME 2026-09-20: the handle survives exclusive fullscreen AND alt-tab-away, so
# IsIconic carries the diagnosis in both display modes. These tests stub the win32 calls --
# what they guard is the REFUSAL discipline around them, which is where the mistakes live.

class _FakeGui:
    def __init__(self, windows):      # windows: [(hwnd, pid, visible, iconic)]
        self._w = windows

    def EnumWindows(self, cb, extra):
        for hwnd, _pid, _vis, _ic in self._w:
            cb(hwnd, extra)

    def IsWindowVisible(self, hwnd):
        return next(v for h, _p, v, _i in self._w if h == hwnd)

    def IsIconic(self, hwnd):
        return 1 if next(i for h, _p, _v, i in self._w if h == hwnd) else 0


class _FakeProc:
    def __init__(self, windows):
        self._w = windows

    def GetWindowThreadProcessId(self, hwnd):
        return (0, next(p for h, p, _v, _i in self._w if h == hwnd))


def _with_windows(monkeypatch, pids, windows):
    import sys
    from x4validate import _livedump
    monkeypatch.setattr(_livedump, "game_pids", lambda: pids)
    monkeypatch.setitem(sys.modules, "win32gui", _FakeGui(windows))
    monkeypatch.setitem(sys.modules, "win32process", _FakeProc(windows))


def test_a_MINIMIZED_window_of_the_game_process_reports_True(monkeypatch):
    _with_windows(monkeypatch, [77], [(1, 77, True, True)])
    assert lp.game_is_minimized() is True


def test_a_RESTORED_window_reports_False(monkeypatch):
    """The twin: without it, a function that always answered True would pass the test above."""
    _with_windows(monkeypatch, [77], [(1, 77, True, False)])
    assert lp.game_is_minimized() is False


def test_ONE_non_iconic_window_REFUTES_minimized(monkeypatch):
    """ALL, not ANY. X4 can own more than one visible top-level window, and the frame loop
    stops only when the game itself is down -- so one restored window settles it."""
    _with_windows(monkeypatch, [77], [(1, 77, True, True), (2, 77, True, False)])
    assert lp.game_is_minimized() is False


def test_windows_of_ANOTHER_process_are_not_evidence_about_X4(monkeypatch):
    """A minimized window belonging to something else must not be reported as the game's.
    With no window of OUR pid left, the honest answer is None, not False."""
    _with_windows(monkeypatch, [77], [(1, 999, True, True)])
    assert lp.game_is_minimized() is None


def test_an_INVISIBLE_window_is_skipped(monkeypatch):
    """X4 owns hidden helper windows whose iconic state says nothing about the game."""
    _with_windows(monkeypatch, [77], [(1, 77, False, True)])
    assert lp.game_is_minimized() is None


def test_NO_WINDOW_FOUND_refuses_rather_than_reporting_NOT_minimized(monkeypatch):
    """★ CLAUDE.md #34. "I found no window to ask about" is a fact about the SEARCH, not
    about the window. Answering False here would put the second claim in the first's grammar
    -- and False is an ACTIONABLE answer ("stop looking at the window"), so it would actively
    mislead."""
    _with_windows(monkeypatch, [77], [])
    assert lp.game_is_minimized() is None


def test_a_CLOSED_game_refuses(monkeypatch):
    _with_windows(monkeypatch, [], [(1, 77, True, True)])
    assert lp.game_is_minimized() is None


def test_an_UNDETERMINED_process_list_refuses(monkeypatch):
    """game_pids answers None when it could not ask. That must not become "no pids"."""
    _with_windows(monkeypatch, None, [(1, 77, True, True)])
    assert lp.game_is_minimized() is None


def test_MISSING_pywin32_refuses_instead_of_raising(monkeypatch):
    """pywin32 is a dev-only, Windows-only extra. Its absence costs this one diagnosis; it
    must not cost the refusal message it is part of."""
    import sys
    from x4validate import _livedump
    monkeypatch.setattr(_livedump, "game_pids", lambda: [77])
    monkeypatch.setitem(sys.modules, "win32gui", None)      # import ... -> ImportError
    monkeypatch.setitem(sys.modules, "win32process", None)
    assert lp.game_is_minimized() is None


# --- the message the user actually reads ------------------------------------ #

def test_NOT_RUNNING_names_the_game_and_does_not_mention_the_mod():
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=False, loaded=None, deployed=None, minimized=None)
    assert "NOT RUNNING" in msg
    assert "minimi" not in msg.lower(), "a closed game must not get the minimized essay"


def test_RUNNING_and_LOADED_points_at_the_frame_loop_not_the_deployment():
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=True, deployed=True, minimized=None)
    assert "contains a helper load marker" in msg
    assert "does not establish the current process or session" in msg
    assert "DID load this session" not in msg
    assert "MINIMIZE" in msg, "this is the case the minimized hint is for"


def test_NOT_DEPLOYED_points_at_the_install(tmp_path):
    """The narrowing the deployment read exists for: this user should be told to check the
    install, NOT to un-minimize a window."""
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=False, deployed=False, minimized=None)
    assert "NOT in the set the engine would load" in msg and "extensions" in msg
    assert "MINIMIZE" not in msg, (
        "a mod that never loaded is not a minimized-window problem; sending that user to the "
        "window is the exact mis-diagnosis this replaces")


def test_NOT_DEPLOYED_outranks_an_UNKNOWN_marker():
    """A log we could not read must not downgrade a deployment we COULD measure."""
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=None, deployed=False, minimized=None)
    assert "NOT in the set the engine would load" in msg
    assert "MINIMIZE" not in msg


def test_a_LOADED_marker_OUTRANKS_a_not_deployed_read():
    """The twin that stops the branch above over-reaching. A mod cannot log from a live run
    without being installed, so this combination means the mod set moved under the running
    game -- it is NOT evidence that the mod never loaded."""
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=True, deployed=False, minimized=None)
    assert "contains a helper load marker" in msg and "NOT in the set the engine would load" not in msg


def test_MENU_no_save_loaded_does_NOT_say_not_installed():
    """★ THE DEFECT THIS FIXES, found in game 2026-09-20. At the main menu the mod is installed
    and enabled and its marker is legitimately absent, because it initialises on GAME LOAD. The
    old text concluded "most likely not installed" and sent the user to check their install."""
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=False, deployed=True, minimized=None)
    assert "IS installed and enabled" in msg
    assert "GAME LOAD" in msg and "save" in msg
    assert "not installed" not in msg, "the exact false positive this replaces"
    assert "MINIMIZE" not in msg


def test_marker_absent_and_deployment_UNKNOWN_names_BOTH_causes():
    """With no deployment reading there is no evidence separating the two causes, so the
    message must name both. Picking one is what made the menu case wrong."""
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=False, deployed=None, minimized=None)
    assert "TWO possibilities" in msg
    assert "load a save" in msg.lower() and "extensions" in msg


def test_RUNNING_but_marker_UNKNOWN_keeps_the_old_hedged_text():
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 10.0,
                                   running=True, loaded=None, deployed=True, minimized=None)
    assert "MINIMIZE" in msg and "did NOT log" not in msg


@pytest.mark.parametrize("loaded", [True, False, None])
@pytest.mark.parametrize("deployed", [True, False, None])
def test_every_message_names_the_pipe_and_the_timeout(loaded, deployed):
    msg = lp._no_connection_reason(r"\\.\pipe\x4live", 7.0,
                                   running=True, loaded=loaded, deployed=deployed, minimized=None)
    assert r"\\.\pipe\x4live" in msg and "7" in msg


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("false", False), ("unknown", None), ("1", None),
    ("trueish", None), ("TRUE", None), ("", None),
])
def test_protected_mode_requires_explicit_boolean_marker(tmp_path, monkeypatch, value, expected):
    _with_log(monkeypatch, _log(tmp_path, f"X4TOOLKIT{MARKER}: proto=1 ui_safe_mode={value}\n"))
    assert lp.helper_ui_safe_mode_at_load() is expected


@pytest.mark.parametrize("last", [
    f"X4TOOLKIT{MARKER}: ui_safe_mode=false",
    f"X4TOOLKIT{MARKER}: ui_safe_mode=unknown",
    f"X4TOOLKIT{MARKER}: proto=1",
    f"X4TOOLKIT{MARKER}: ui_safe_mode=true ui_safe_mode=false",
])
def test_latest_marker_supersedes_earlier_true(tmp_path, monkeypatch, last):
    body = f"X4TOOLKIT{MARKER}: ui_safe_mode=true\n{last}\n"
    _with_log(monkeypatch, _log(tmp_path, body))
    expected = False if last.endswith("ui_safe_mode=false") and last.count("ui_safe_mode=") == 1 else None
    assert lp.helper_ui_safe_mode_at_load() is expected


@pytest.mark.parametrize("body", [
    f"X4TOOLKIT{MARKER}: ui_safe_mode=true\n",
    f"X4TOOLKIT{MARKER}: ui_safe_mode=false\n",
])
def test_stale_mode_evidence_is_unknown(tmp_path, monkeypatch, body):
    _with_log(monkeypatch, _log(tmp_path, body, age_s=lp.LOG_LIVE_WINDOW_S + 60))
    assert lp.helper_ui_safe_mode_at_load() is None


def test_mode_field_without_a_marker_is_not_evidence(tmp_path, monkeypatch):
    _with_log(monkeypatch, _log(tmp_path, "ui_safe_mode=true\n"))
    assert lp.helper_ui_safe_mode_at_load() is None


def test_unreadable_mode_log_is_unknown(tmp_path, monkeypatch):
    _with_log(monkeypatch, tmp_path / "missing")
    assert lp.helper_ui_safe_mode_at_load() is None
    _with_log(monkeypatch, None)
    assert lp.helper_ui_safe_mode_at_load() is None


def test_explicit_mode_marker_names_user_choice_not_a_hung_game(tmp_path, monkeypatch):
    _with_log(monkeypatch, _log(tmp_path, f"X4TOOLKIT{MARKER}: ui_safe_mode=true\n"))
    msg = lp._no_connection_reason("pipe", 7, running=True, loaded=True, deployed=True,
                                  minimized=False,
                                  ui_safe_mode_at_load=lp.helper_ui_safe_mode_at_load())
    assert "Protected UI Mode ON at load" in msg and "your choice" in msg
    assert "does not prove the setting is still on" in msg
    assert "hung" not in msg and "not EXECUTING" not in msg


@pytest.mark.parametrize("mode", [False, None])
def test_false_or_unknown_mode_does_not_invent_a_cause(mode):
    msg = lp._no_connection_reason("pipe", 7, running=True, loaded=True, deployed=True,
                                  minimized=False, ui_safe_mode_at_load=mode)
    assert "connection was not established" in msg
    assert "Protected UI Mode ON" not in msg and "not EXECUTING" not in msg
    assert "does not establish that the game is hung" in msg


def test_mode_evidence_does_not_override_closed_game():
    msg = lp._no_connection_reason("pipe", 7, running=False, loaded=True, deployed=True,
                                  minimized=False, ui_safe_mode_at_load=True)
    assert "NOT RUNNING" in msg and "Protected UI Mode ON" not in msg


def test_mode_diagnosis_preserves_measured_minimized_state():
    msg = lp._no_connection_reason("pipe", 7, running=True, loaded=True, deployed=True,
                                  minimized=True, ui_safe_mode_at_load=True)
    assert "Protected UI Mode ON at load" in msg and "window is MINIMIZED" in msg


def test_actual_timeout_path_consumes_explicit_mode_marker(tmp_path, monkeypatch):
    from types import SimpleNamespace

    def listening(*args):
        error = OSError("still listening")
        error.winerror = 536
        raise error

    monkeypatch.setattr(lp, "_win32", lambda: (
        SimpleNamespace(ConnectNamedPipe=listening), None,
        SimpleNamespace(ERROR_PIPE_CONNECTED=535, ERROR_PIPE_LISTENING=536, ERROR_NO_DATA=232)))
    monkeypatch.setattr(lp, "game_is_running", lambda: True)
    monkeypatch.setattr(lp, "game_is_minimized", lambda: False)
    monkeypatch.setattr(lp, "helper_deployment", lambda: (True, None))
    _with_log(monkeypatch, _log(tmp_path, f"X4TOOLKIT{MARKER}: ui_safe_mode=true\n"))
    pipe = lp.LivePipe(name="test", timeout=0)
    with pytest.raises(lp.LiveQueryUnavailable, match="Protected UI Mode ON at load"):
        pipe.wait_for_game()
    assert not pipe._connected

"""conftest's faulthandler scoping: off only around Lua-runtime modules, and back on after."""
import faulthandler
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def plugin_on(request):
    if not request.config.pluginmanager.has_plugin("faulthandler"):
        pytest.skip("run with -p no:faulthandler")


def test_faulthandler_stays_on_outside_the_lua_test_modules(plugin_on):
    assert faulthandler.is_enabled()


def test_lua_module_turns_it_off_and_back_on(plugin_on, faulthandler_scope):
    # Delta review minor 6: exercise the re-enable directly, not by test order.
    assert faulthandler.is_enabled()
    with faulthandler_scope(HERE / "test_livemdchannel.py") as was:
        assert was is True and not faulthandler.is_enabled()
    assert faulthandler.is_enabled()


@pytest.mark.parametrize("name", ["test_liveaudio.py", "test_livefocus.py"])
def test_native_ctypes_modules_keep_it_on(plugin_on, faulthandler_scope, name):
    with faulthandler_scope(HERE / name) as was:
        assert was is False and faulthandler.is_enabled()

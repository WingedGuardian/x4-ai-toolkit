"""Twin of conftest's _quiet_luajit_seh_dumps: modules that run no game Lua keep faulthandler."""
import faulthandler

import pytest


def test_faulthandler_stays_on_outside_the_lua_test_modules(request):
    if not request.config.pluginmanager.has_plugin("faulthandler"):
        pytest.skip("run with -p no:faulthandler")
    assert faulthandler.is_enabled()

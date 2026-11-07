"""Give toolkit pytest invocations owned scratch, without shared numbered-dir pruning."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from x4temp import mark_owned_temp

#: The marker a FAILED test leaves, so x4housekeep recycles (never deletes) a session folder holding failure evidence
#: (release-session policy, 4.1.1). Same name as scripts/x4housekeep.py KEPT_FAILURE.
KEPT_FAILURE = ".x4-kept-failure"
_MARKER_PLUGIN = "x4pytest-kept-failure"


def mark_kept_failure(folder):
    try:
        (Path(folder) / KEPT_FAILURE).write_bytes(b"")
    except OSError:
        pass  # silent-ok: a missing marker leaves the folder to the ordinary rules; the failure itself is reported


class _KeptFailure:
    """4.1.1 review B1: one marker hook for every toolkit suite. It lived in tools/x4validate/tests/conftest.py only,
    so the BaseX suite marked nothing, and a failing test that wrote through tmp_path_factory had no tmp_path to mark.
    A failure now marks the test's tmp_path AND the session's base folder."""

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        if outcome.get_result().failed:
            tmp = getattr(item, "funcargs", {}).get("tmp_path")
            if tmp is not None and Path(tmp).is_dir():
                mark_kept_failure(tmp)
            base = getattr(getattr(item.config, "_tmp_path_factory", None), "_basetemp", None)
            if base is not None and Path(base).is_dir():
                mark_kept_failure(base)


def configure(config, toolkit):
    if getattr(config, "_x4_temp_configured", False):
        return
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is not None and factory._basetemp is not None:
        # A plugin initialized the global numbered root too early. Cancel its
        # deferred pruning rather than run it while refusing this session.
        factory._exit_stack.pop_all()
        raise pytest.UsageError("pytest temp storage initialized before toolkit isolation; "
                                "use a new directory with --basetemp or review plugin order")
    if not config.pluginmanager.has_plugin(_MARKER_PLUGIN):
        config.pluginmanager.register(_KeptFailure(), _MARKER_PLUGIN)
    given = config.option.basetemp
    if given is not None:
        if os.path.lexists(given):
            raise pytest.UsageError("--basetemp must name a new directory; earlier runs are preserved")
        config._x4_temp_configured = True
        return
    owned = tempfile.TemporaryDirectory(prefix="x4-pytest-session-")
    config.add_cleanup(owned.cleanup)  # registered at creation, including error exits
    config._x4_owned_temp = owned
    mark_owned_temp(owned.name, toolkit)
    base = Path(owned.name) / "tests"
    config.option.basetemp = str(base)
    if factory is not None:
        factory._given_basetemp = base
    config._x4_temp_configured = True


def session_start(session, toolkit):
    config = session.config
    base = config._tmp_path_factory.getbasetemp()
    if getattr(config, "_x4_receipted_base", None) == base:
        return
    mark_owned_temp(base, toolkit)
    config._x4_receipted_base = base
    os.environ["X4_RECYCLE_TEST_ROOT"] = str(base)
    os.environ["X4_RECYCLE_TEST_BIN"] = str(base / ".fake-recycle-bin")
    os.environ["X4_HOUSEKEEP_SWEEP"] = "off"

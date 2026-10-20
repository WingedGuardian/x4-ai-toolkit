"""Give toolkit pytest invocations owned scratch, without shared numbered-dir pruning."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from x4temp import mark_owned_temp


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

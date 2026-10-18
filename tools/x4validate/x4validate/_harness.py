"""Load this toolkit's offline pinned core without sharing another install's module."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path


class HarnessMissing(RuntimeError):
    """The pinned harness core (`tools/modding_harness`) is not where it ships."""


def _package() -> Path:
    return Path(__file__).resolve().parents[2] / "modding_harness"


def module(name):
    package = _package()
    if not package.is_dir():
        raise HarnessMissing("the pinned offline harness core is missing; repair this toolkit installation")
    namespace = "_x4_harness_" + hashlib.sha256(str(package).encode()).hexdigest()[:16]
    if namespace not in sys.modules:
        spec = importlib.util.spec_from_file_location(namespace, package / "__init__.py",
                                                    submodule_search_locations=[str(package)])
        core = importlib.util.module_from_spec(spec)
        sys.modules[namespace] = core
        try:
            spec.loader.exec_module(core)
        except BaseException:
            sys.modules.pop(namespace, None)
            raise
    return importlib.import_module(namespace + "." + name)

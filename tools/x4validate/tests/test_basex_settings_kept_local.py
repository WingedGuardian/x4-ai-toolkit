"""tools/basex/basex/.basex is BaseX's settings file (DBPATH, REPOPATH, ports), not a cache (user ruling 2026-11-26).

It must never travel from a source tree (it names that machine's paths) and never be removed from an install
(a moved DBPATH would silently reset and the databases would look missing). So it is keep-local in both
installers, and in neither prune list: a pruned path is also deleted from the destination.
"""
import re

from codex_testlib import REPO


def _sh_list(name):
    return re.search(r'^%s="([^"]*)"' % name, (REPO / "install.sh").read_text(encoding="utf-8"), re.M).group(1).split()


def _ps_list(name):
    text = (REPO / "install.ps1").read_text(encoding="utf-8")
    body = re.search(r"^\$%s = @\((.*?)\)" % name, text, re.M | re.S).group(1)
    return [item.replace("\\", "/") for item in re.findall(r"'([^']+)'", body)]


def test_basex_settings_are_keep_local_in_both_installers():
    assert "tools/basex/basex/.basex" in _sh_list("X4_KEEP_LOCAL")
    assert "tools/basex/basex/.basex" in _ps_list("X4KeepLocal")


def test_TWIN_basex_settings_are_in_neither_prune_list():
    assert "tools/basex/basex/.basex" not in _sh_list("X4_COPY_PRUNE")
    assert "tools/basex/basex/.basex" not in _ps_list("X4CopyPrune")

"""A machine-local baseline the package gitignores must never be copied by an installer.

`.gitignore` already says which baselines are machine-local; the installers' prune lists
are a second, hand-kept copy of that fact. Three transcript baselines (one of them ~1.5 MB
holding the opening of every past shell command) were gitignored and absent from both
prune lists when their gates arrived (review, 2026-09-14). This derives the requirement
from `.gitignore` so the next baseline cannot slip the same way.
"""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
REPO = PKG.parent.parent
BS = chr(92)


def ignored_baselines(gitignore: str) -> list[str]:
    return sorted({m.group(0) for m in re.finditer(r"(?m)^\.[a-z0-9-]+-baseline\.json$", gitignore.replace(chr(13), ""))})


def unpruned(gitignore: str, sh: str, ps1: str) -> list[str]:
    """Baselines must stay local, without being removed from the destination."""
    sh_list = re.search(r'X4_KEEP_LOCAL="([^"]*)"', sh)
    ps_list = re.search(r"\$X4KeepLocal = @\((.*?)\)", ps1, re.S)
    assert sh_list and ps_list, "a keep-local list could not be located, so nothing was checked"
    sh_items = set(sh_list.group(1).split())
    ps_items = set(re.findall(r"'([^']+)'", ps_list.group(1)))
    rows = []
    for name in ignored_baselines(gitignore):
        if "tools/x4validate/" + name not in sh_items:
            rows.append("install.sh: " + name)
        if "tools" + BS + "x4validate" + BS + name not in ps_items:
            rows.append("install.ps1: " + name)
    return rows


def _read(p: Path) -> str:
    return p.read_bytes().decode("utf-8")


def test_every_ignored_baseline_is_pruned_by_both_installers():
    gi = _read(PKG / ".gitignore")
    assert len(ignored_baselines(gi)) >= 5, ignored_baselines(gi)
    assert unpruned(gi, _read(REPO / "install.sh"), _read(REPO / "install.ps1")) == []


def test_TWIN_a_baseline_missing_from_one_installer_is_reported():
    gi = ".a-baseline.json" + chr(13) + chr(10) + ".b-baseline.json" + chr(10)
    sh = 'X4_KEEP_LOCAL="tools/x4validate/.a-baseline.json tools/x4validate/.b-baseline.json"'
    ps1 = "$X4KeepLocal = @('tools" + BS + "x4validate" + BS + ".a-baseline.json')"
    assert unpruned(gi, sh, ps1) == ["install.ps1: .b-baseline.json"]

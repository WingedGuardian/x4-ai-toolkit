"""The shipped resource-policy text states the headroom rule, not the retired share rule.

4.3.1 (user decision 2026-11-26): a job we start waits only when it would push the machine past
90% CPU, leave under 3 GiB of RAM (20% of RAM while a game runs) or under 512 MiB of commit.
The old "together at most 40% of CPU and 25% of RAM" shares are gone; the disk floors, the
emergency stop floors, low priority and the eight-worker cap stay. The text ships publicly, so
it names no person, machine or private path either.
"""
from __future__ import annotations

import re

import pytest

from _layout import ROOT, require_repo

CORE = "agent/instructions/core.md"
HEADROOM = "tools/x4validate/docs/HEADROOM-GATES.md"
AGENTS_LIMIT = 32768  # Codex's AGENTS.md ceiling


def _text(rel: str) -> str:
    require_repo(rel, why="shipped policy text")
    # Whitespace-normalised: the source is hard-wrapped, so a phrase may span a line break.
    return " ".join((ROOT / rel).read_bytes().decode("utf-8").split())


@pytest.mark.parametrize("rel", [CORE, HEADROOM])
def test_states_the_headroom_rule(rel):
    t = _text(rel)
    if rel == CORE:
        t = t.split("**Resources:", 1)[1].split("### Iteration snapshots", 1)[0]
    assert "90%" in t and "3 GiB" in t and "512 MiB" in t


@pytest.mark.parametrize("rel", [CORE, HEADROOM])
def test_retired_shares_are_gone(rel):
    t = _text(rel)
    assert "40% of CPU" not in t and "25% of RAM" not in t
    assert not re.search(r"\b40% total\b|\b25% of RAM \(", t)


@pytest.mark.parametrize("rel", [CORE, HEADROOM])
def test_the_disk_floors_and_worker_cap_stay(rel):
    t = _text(rel).replace("GiB", "GB")
    assert "min(pagefile, 50 GB)" in t and "min(5%, 50 GB)" in t
    assert "eight" in t


def test_core_keeps_the_stop_floors():
    t = _text(CORE)
    assert "256 MiB" in t and "128 MiB" in t and "2 GiB" in t and "1 GiB" in t


def test_core_names_the_game_running_figure():
    assert "20% of RAM while a game" in _text(CORE)


def test_agents_md_stays_inside_codexs_limit():
    require_repo("AGENTS.md", why="generated entry file")
    n = len((ROOT / "AGENTS.md").read_bytes())
    assert n <= AGENTS_LIMIT, f"AGENTS.md is {n} bytes, over {AGENTS_LIMIT}"

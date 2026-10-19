"""The fuzzer's verdict map must cover every rule protect-bash.sh actually has.

It used to be two hand-written tuples and they had drifted. MEASURED 2026-09-01: the
hook maps 19 predicates, the tuples listed 14, so the fuzzer was blind to 5 rules (26%)
and mis-classified a 6th. A seed hitting an unmapped rule reads "already allow" and is
SKIPPED IN SILENCE -- which is how 2 of 12 seeds were dropped from every run, and why a
bypass in those five rules could never have been reported.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _fz():
    spec = importlib.util.spec_from_file_location("fz_undertest", ROOT / "scripts" / "fuzz-guard.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["fz_undertest"] = m
    spec.loader.exec_module(m)
    return m


def _names(m):
    probe = m.load_facts(m.HOOKS / "hook_facts.py")
    return set(probe.facts({"tool_input": {"command": "true", "timeout": 0,
                                           "run_in_background": False}}, m.SYNTHETIC_ROOTS))


def test_every_rule_in_the_hook_carries_a_verdict():
    m = _fz()
    policy = m.policy_map(_names(m))
    assert len(policy) >= m.MIN_MAPPED_RULES, (
        "only %d rules parsed out of protect-bash.sh; the map is derived from that file "
        "and a short parse makes every seed read 'allow'" % len(policy))
    assert all(v in ("deny", "ask", "advise") for v in policy.values()), policy


def test_no_predicate_is_silently_unfuzzed():
    """Any fact hook_facts emits is either mapped to a verdict or is metadata. A new
    predicate that nobody wires into protect-bash.sh should be visible, not invisible."""
    m = _fz()
    names = _names(m)
    policy = m.policy_map(names)
    # `from_powershell` is a LABEL, not a rule: protect-bash.sh uses it to say the quoted
    # command is a translation of the PowerShell the caller typed (AUDIT-2026-09-24 HK-1).
    # `save_lift_blockers` (K1, v4.0) is the SAVE BRIDGE's input, not a Bash rule: hook_facts
    # pops it before the protect-bash.sh IPC and save_policy.py turns it into an opaque
    # evidence row (pinned in test_shared_save_policy.py). The exemption holds only while
    # that consumer exists -- asserted below, so a dead consumer cannot hide behind it.
    BRIDGE = {"save_lift_blockers"}
    save_policy = (m.HOOKS / "save_policy.py").read_text(encoding="utf-8")
    for name in BRIDGE:
        assert 'facts.get("%s")' % name in save_policy, (
            "%s is exempt as save-bridge input, but save_policy.py no longer reads it" % name)
    METADATA = {"command", "cwd", "timeout", "background", "from_powershell"} | BRIDGE
    unmapped = sorted(names - set(policy) - METADATA)
    assert not unmapped, (
        "these facts are computed but carry no verdict, so the fuzzer cannot test them "
        "and the hook does nothing with them: %s" % unmapped)


def test_the_map_refuses_rather_than_degrades(tmp_path, monkeypatch):
    """The branch that matters: an empty map would make every seed 'allow', every seed
    would be skipped as nothing-to-weaken, and the run would print 'no bypass found'
    over having exercised nothing at all."""
    m = _fz()
    names = _names(m)
    (tmp_path / "protect-bash.sh").write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    monkeypatch.setattr(m, "HOOKS", tmp_path)
    assert len(m.policy_map(names)) < m.MIN_MAPPED_RULES


def test_every_control_anchor_still_plants():
    """The fuzzer's control re-creates the pre-fix scanner, and it REFUSES (rc 2) if
    any anchor has moved -- correctly, because a control that plants three of four
    holes is not a control. But nothing asserted it at commit time, so an ordinary
    refactor could orphan an anchor and the only thing that noticed was CI.

    MEASURED 2026-09-09: it noticed on the v3.1.0 TAG. `durable_python_open_w`'s
    iteration moved onto resolved segments, the anchor went to 0 occurrences, and
    `fuzz-guard.py` exited 2 on BOTH CI legs of the released commit -- a tool the
    release notes name as evidence users can run themselves. That was the THIRD
    anchor to die this way. This test is the check that was missing: it costs
    milliseconds and it fails in the same commit that moves the code.
    """
    m = _fz()
    src = (m.HOOKS / "hook_facts.py").read_text(encoding="utf-8")
    holed = m.plant_known_hole(src)
    assert holed is not None, (
        "at least one control anchor in fuzz-guard.py:_HOLES no longer matches "
        "hook_facts.py exactly once -- run `python scripts/fuzz-guard.py` to see which"
    )
    assert holed != src, "the control planted nothing at all"
    for label, scope, old, new in m._HOLES:
        assert new in holed, "hole %r did not land" % (label,)


def test_every_reachable_policy_rule_is_exercised_by_a_seed():
    """The run refuses below this floor too -- but only in CI, minutes in, and only
    if someone reads the leg. MEASURED 2026-09-09 at the v3.1.0 tag: `verb_unresolved`
    shipped with NO seed. That is the rule added to close a total bypass of all three
    hard blocks, so the one rule written to fix a walkable guard was the one rule no
    mutant ever exercised. Adding its seed found a real DENY -> ALLOW within seconds.
    """
    m = _fz()
    policy = m.policy_map(_names(m))
    covered = m.seed_coverage(m.load_facts(m.HOOKS / "hook_facts.py"),
                              m.SYNTHETIC_ROOTS, policy)
    gap = sorted(set(policy) - m.STRUCTURAL - set(covered))
    assert not gap, (
        "%d policy rule(s) a seed could reach have none, so a green fuzz run says "
        "nothing about them: %s" % (len(gap), ", ".join(gap)))
    stale = sorted(m.STRUCTURAL - set(policy))
    assert not stale, (
        "STRUCTURAL exempts %s, which protect-bash.sh no longer maps -- an exemption "
        "for a rule that does not exist silently lowers the floor" % ", ".join(stale))

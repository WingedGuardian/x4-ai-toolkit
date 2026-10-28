"""scripts/verify-hook-tests.py's own lookups (FX-G5 / reviewer J2 item 8).

The silent? column credits a failing must-NOT-fire test to a predicate only when that TEST's
source names the predicate. Failures arrive qualified ("Class.method", FX-G3) and the target
index is qualified too, but the source table was keyed by the bare METHOD name, so two classes
sharing a method name pooled their sources: a red test in class B was credited with the
predicate class A's same-named test mentions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("verify_hook_tests", ROOT / "scripts" / "verify-hook-tests.py")
vht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vht)

SRC = '''
import unittest


class A(unittest.TestCase):
    KEYS = ("x",)

    def test_same(self):
        self.assertFalse(F("x")["rm_hits_game"])


class B(unittest.TestCase):
    def test_same(self):
        self.assertFalse(F("x")["writes_reference"])


def test_module_level():
    assert "git_add_all"
'''


def test_sources_are_keyed_by_the_QUALIFIED_name():
    srcs = vht.test_sources(SRC)
    assert vht.names_predicate(srcs["A.test_same"], "rm_hits_game")
    assert not vht.names_predicate(srcs["B.test_same"], "rm_hits_game")     # J2: was pooled
    assert vht.names_predicate(srcs["B.test_same"], "writes_reference")
    assert 'KEYS = ("x",)' in srcs["A.test_same"]                           # class attrs travel
    assert 'KEYS' not in srcs["B.test_same"]
    assert vht.names_predicate(srcs["test_module_level"], "git_add_all")


def test_a_failure_is_looked_up_by_the_name_failed_tests_reports():
    out = ("FAIL: test_same (__main__.B.test_same)\n"
           "ERROR: test_same (__main__.A)\n")
    failed = vht.failed_tests(out)
    assert failed == {"B.test_same", "A.test_same"}
    srcs = vht.test_sources(SRC)
    assert all(t in srcs for t in failed)
    # the column's own predicate: only A's test is ABOUT rm_hits_game
    assert vht.credited(failed, srcs, "rm_hits_game") == {"A.test_same"}
    assert vht.credited({"B.test_same"}, srcs, "rm_hits_game") == set()


# --------------------------------------------------- CI shards (4.0.1, ci/v4.0.1-rc6)
def _population(n_mut, keys):
    return ({"mutation:%d" % i for i in range(n_mut)}
            | {"predicate:%s:%s" % (k, v) for k in keys for v in ("False", "True")})


def test_the_shards_partition_the_whole_population():
    keys = ["a", "b", "c", "d", "e"]
    for n in (1, 2, 3, 4, 7):
        shards = [vht.shard_groups(11, keys, k, n) for k in range(1, n + 1)]
        assert set().union(*shards) == _population(11, keys), n          # nothing dropped
        assert sum(map(len, shards)) == len(_population(11, keys)), n    # nothing twice


def test_a_predicates_two_trials_share_a_shard():
    """The silent?/fires? verdict reads BOTH trials of a predicate; split, neither shard has it."""
    for k in range(1, 5):
        s = vht.shard_groups(3, ["p", "q", "r"], k, 4)
        for key in ("p", "q", "r"):
            assert ("predicate:%s:False" % key in s) == ("predicate:%s:True" % key in s)


def test_TWIN_a_shard_beyond_the_population_is_empty_and_the_argument_is_checked():
    assert vht.shard_groups(1, [], 2, 2) == set()          # main() refuses this (rc 2)
    import argparse
    import pytest
    for bad in ("0/4", "5/4", "x", "1/0"):
        with pytest.raises(argparse.ArgumentTypeError):
            vht._shard_arg(bad)
    assert vht._shard_arg("2/4") == (2, 4)

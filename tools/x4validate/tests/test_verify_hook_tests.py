"""scripts/verify-hook-tests.py's own lookups (FX-G5 / reviewer J2 item 8).

The silent? column credits a failing must-NOT-fire test to a predicate only when that TEST's
source names the predicate. Failures arrive qualified ("Class.method", FX-G3) and the target
index is qualified too, but the source table was keyed by the bare METHOD name, so two classes
sharing a method name pooled their sources: a red test in class B was credited with the
predicate class A's same-named test mentions.
"""
from __future__ import annotations

import importlib.util
import sys
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


# ------------------------------- M14 (4.1 review): the CI shard matrix against the `/N` in its run line
def _ci_shard_config():
    """(matrix shard list, [N of every `--shard K/N` in the hook-mutation job's run lines])"""
    import re

    from ruamel.yaml import YAML
    ci = YAML(typ="safe").load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["hook-mutation"]
    runs = [str(s.get("run", "")) for s in job["steps"]]
    ns = [int(n) for r in runs for n in re.findall(r"--shard\s+\$\{\{\s*matrix\.shard\s*\}\}/(\d+)", r)]
    return list(job["strategy"]["matrix"]["shard"]), ns


def test_the_ci_shard_matrix_is_exactly_the_N_its_run_line_divides_by():
    """ci.yml says 'edit `shard` AND the `/4` in the run line together' and nothing enforced it:
    matrix [1,2,3] with `/4` silently never runs shard 4 (a quarter of the mutants), and [1..5] with
    `/4` runs a shard that main() refuses. The gate passes only when ALL N shards ran."""
    from _layout import require_repo
    require_repo(".github/workflows/ci.yml", why="CI configuration is not installed")
    shards, ns = _ci_shard_config()
    assert len(ns) == 1, "expected exactly one `--shard ${{ matrix.shard }}/N` run line, found %r" % ns
    n = ns[0]
    assert shards == list(range(1, n + 1)), "matrix shard %r is not 1..%d (the /%d in the run line)" % (shards, n, n)
    # every shard is non-empty against the REAL mutant population, so none can pass on nothing
    assert len(vht.MUTANTS) >= n
    for k in shards:
        assert vht.shard_groups(len(vht.MUTANTS), [], k, n), k


def test_TWIN_the_shard_matrix_check_goes_red_on_a_mismatch(monkeypatch):
    """One twin per clause: a matrix short of the divisor, a matrix beyond it, a missing run line."""
    import pytest

    from _layout import require_repo
    require_repo(".github/workflows/ci.yml", why="CI configuration is not installed")
    for shards, ns in (([1, 2, 3], [4]), ([1, 2, 3, 4, 5], [4]), ([1, 2, 3, 4], []), ([1, 2, 3, 4], [4, 4])):
        monkeypatch.setattr(sys.modules[__name__], "_ci_shard_config", lambda s=shards, n=ns: (s, n))
        with pytest.raises(AssertionError):
            test_the_ci_shard_matrix_is_exactly_the_N_its_run_line_divides_by()

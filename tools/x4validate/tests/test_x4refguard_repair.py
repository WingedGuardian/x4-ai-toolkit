r"""x4refguard: never trust a sample or a bare exit code; an interrupted run is repairable by running it again.

MEASURED 2026-11-03 on scratch trees of 40,006 objects, killing a real icacls mid-propagation:
  * killed apply: 2,970 objects carry the inherited deny and the ROOT has NO explicit deny (icacls writes the root last),
    so `status` read `absent`. Re-running /deny finishes it (40,006 of 40,006). /remove:d also clears the strays (0 left),
    though the root carries nothing to remove.
  * killed remove: the root still has the ACE and 2,221 objects have lost it. `status` can still read `protected` (a
    sample). Re-running /deny restores all of them; re-running /remove:d clears all of them.
  * `icacls <missing> ... /C` exits 0 and prints "Failed processing 1 files": the exit code alone is not the answer.
  * a child with inheritance cut: apply exits 0, the sample says protected, the full walk says partial.

THE REAL REFERENCE TREE IS NEVER TOUCHED: X4_REFGUARD_SANDBOX confines every mutation to tmp_path, and the seam tests
call no icacls at all.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from refguard_owner import own_or_skip

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("x4refguard_repair", REPO / "scripts" / "x4refguard.py")
rg = importlib.util.module_from_spec(_spec)
sys.modules["x4refguard_repair"] = rg
_spec.loader.exec_module(rg)

SID = "S-1-5-21-OWNED-FIXTURE"
win = pytest.mark.skipif(os.name != "nt", reason="NTFS ACLs: the Windows mechanism")
OK = "Successfully processed 1 files; Failed processing 0 files"
BAD = "Successfully processed 0 files; Failed processing 1 files"


def _rule(root=False, mask=rg.EXPECTED_MASK):
    return {"sid": SID, "type": 1, "rights": mask, "inherited": not root, "inh": 3, "prop": 0}


class Acl:
    """A tree whose ACLs are data. `denied` is which objects carry the deny; icacls is scripted."""

    def __init__(self, root, children, monkeypatch, denied=(), root_ace=False):
        self.root, self.children = root, list(children)
        self.denied, self.root_ace = set(denied), root_ace
        self.calls = []
        self.next_results = []
        self.outcome = None
        monkeypatch.setattr(rg, "_acl", self.acl)
        monkeypatch.setattr(rg, "_user_sid", lambda: SID)
        monkeypatch.setattr(rg, "_owner_is_user", lambda p: True)
        monkeypatch.setattr(rg, "_walk", lambda r: iter([self.root, *self.children]))
        monkeypatch.setattr(rg, "_first_file", lambda d: self.children[0] if self.children else None)
        monkeypatch.setattr(rg, "_objects", self.objects)
        monkeypatch.setattr(rg, "_mutate_run", self.mutate)

    def objects(self, root, full):
        return [self.root, *self.children] if full else [self.root, self.children[0]]

    def acl(self, paths):
        items = []
        for p in map(Path, paths):
            rules = []
            if p == self.root and self.root_ace:
                rules = [_rule(root=True)]
            elif p != self.root and p in self.denied:
                rules = [_rule()]
            items.append({"path": str(p), "owner": SID, "rules": rules, "error": None})
        return SID, items

    def mutate(self, argv, target):
        self.calls.append([str(a) for a in argv])
        rc, out, err = (self.next_results.pop(0) if self.next_results else (0, OK, ""))
        if self.outcome:
            self.outcome(self, argv)
        elif rc == 0 and not err:
            if "/deny" in argv:
                self.root_ace, self.denied = True, set(self.children)
            else:
                self.root_ace, self.denied = False, set()
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr=err)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "ref"
    kids = [root / "a.xml", root / "b.xml", root / "c.xml"]
    return Acl(root, kids, monkeypatch), root, kids


# ---------------------------------------------------------------- the exit code and the output

@pytest.mark.parametrize("rc,out,err", [(1332, BAD, "No mapping between account names and security IDs was done."),
                                        (0, BAD, ""), (0, BAD, "C:\\x: The system cannot find the file specified."),
                                        (0, OK, "x: Access is denied."), (5, OK, "")])
def test_a_failing_icacls_fails_the_apply_whatever_the_reread_says(tree, capsys, rc, out, err):
    acl, root, kids = tree
    def tree_looks_fine(self, argv):
        self.root_ace, self.denied = True, set(self.children)
    acl.outcome = tree_looks_fine
    acl.next_results = [(rc, out, err)]
    assert rg._win_apply(root, yes=True) == 1
    assert "FAILED" in capsys.readouterr().err


def test_a_clean_icacls_with_every_object_verified_succeeds(tree, capsys):
    acl, root, kids = tree
    assert rg._win_apply(root, yes=True) == 0
    assert "applied" in capsys.readouterr().out


@pytest.mark.parametrize("rc,out,err", [(1332, BAD, "No mapping."), (0, BAD, ""), (0, OK, "x: Access is denied.")])
def test_a_failing_icacls_fails_the_remove_whatever_the_reread_says(tree, capsys, rc, out, err):
    acl, root, kids = tree
    acl.root_ace, acl.denied = True, set(kids)
    def tree_looks_clean(self, argv):
        self.root_ace, self.denied = False, set()
    acl.outcome = tree_looks_clean
    acl.next_results = [(rc, out, err)]
    assert rg._win_remove(root, yes=True) == 1
    assert "FAILED" in capsys.readouterr().err


# ---------------------------------------------------------------- never a sample

def test_apply_verifies_every_object_not_a_sample(tree, capsys):
    acl, root, kids = tree
    def misses_the_last_child(self, argv):
        self.root_ace, self.denied = True, set(self.children[:-1])
    acl.outcome = misses_the_last_child
    assert rg._win_apply(root, yes=True) == 1
    assert "c.xml" in capsys.readouterr().err


def test_a_killed_remove_is_not_reported_already_protected(tree, capsys):
    # The measured state: root keeps the ACE, some objects lost it, the sample still says protected.
    acl, root, kids = tree
    acl.root_ace, acl.denied = True, set(kids[:-1])
    assert rg._win_report(root, False)["state"] == "protected"     # the trap
    assert rg._win_apply(root, yes=True) == 0
    assert acl.calls and "/deny" in acl.calls[0]                   # it ran icacls again
    assert acl.denied == set(kids)
    assert "already protected" not in capsys.readouterr().out


def test_a_fully_verified_tree_is_not_written_again(tree, capsys):
    acl, root, kids = tree
    acl.root_ace, acl.denied = True, set(kids)
    assert rg._win_apply(root, yes=True) == 0
    assert acl.calls == []
    assert "already protected" in capsys.readouterr().out


def test_a_killed_apply_reads_partial_not_absent(tree):
    # The measured state: the root has no explicit deny yet, some objects already carry the inherited one.
    acl, root, kids = tree
    acl.denied = set(kids[:1])
    state = rg._win_report(root, False)
    assert state["state"] == "partial" and "interrupted" in state["detail"]


def _inherited_from_above(monkeypatch, acl):
    real = acl.acl

    def acl_with_parent_deny(paths):
        sid, items = real(paths)
        for it in items:
            it["rules"] = [_rule()]
        return sid, items
    monkeypatch.setattr(rg, "_acl", acl_with_parent_deny)


def test_a_deny_inherited_from_above_the_root_is_not_an_interrupted_run(tree, monkeypatch):
    acl, root, kids = tree
    _inherited_from_above(monkeypatch, acl)
    assert rg._win_report(root, False)["state"] == "absent"


def test_remove_leaves_a_deny_inherited_from_above_the_root_alone(tree, monkeypatch, capsys):
    acl, root, kids = tree
    _inherited_from_above(monkeypatch, acl)
    assert rg._win_remove(root, yes=True) == 0
    assert acl.calls == []
    assert "nothing to remove" in capsys.readouterr().out


def test_a_clean_tree_still_reads_absent(tree):
    acl, root, kids = tree
    assert rg._win_report(root, False)["state"] == "absent"


def test_a_killed_apply_is_finished_by_a_second_apply(tree):
    acl, root, kids = tree
    acl.denied = set(kids[:1])
    assert rg._win_apply(root, yes=True) == 0
    assert acl.denied == set(kids) and acl.root_ace


# ---------------------------------------------------------------- remove verifies the children

def test_remove_fails_when_children_keep_the_deny(tree, capsys):
    acl, root, kids = tree
    acl.root_ace, acl.denied = True, set(kids)
    def root_only(self, argv):
        self.root_ace = False                                       # children untouched: a killed propagation
    acl.outcome = root_only
    assert rg._win_remove(root, yes=True) == 1
    err = capsys.readouterr().err
    assert "3" in err and "a.xml" in err and "remove" in err


def test_remove_with_a_clean_root_and_stray_children_repairs_them(tree, capsys):
    # The measured killed-apply state. "Nothing to remove" here left denied objects nobody could lift.
    acl, root, kids = tree
    acl.denied = set(kids[:1])
    assert rg._win_remove(root, yes=True) == 0
    assert acl.calls and "/remove:d" in acl.calls[0]
    assert acl.denied == set()
    assert "nothing to remove" not in capsys.readouterr().out


def test_remove_on_a_truly_clean_tree_writes_nothing(tree, capsys):
    acl, root, kids = tree
    assert rg._win_remove(root, yes=True) == 0
    assert acl.calls == []
    assert "nothing to remove" in capsys.readouterr().out


def test_remove_cannot_verify_an_unreadable_child(tree, monkeypatch, capsys):
    acl, root, kids = tree
    acl.root_ace, acl.denied = True, set(kids)
    real = acl.acl

    def unreadable(paths):
        sid, items = real(paths)
        if not acl.root_ace and len(items) > 1:                     # the walk, not the root's own read
            items[-1]["error"] = "Access denied"
        return sid, items
    monkeypatch.setattr(rg, "_acl", unreadable)
    assert rg._win_remove(root, yes=True) == 1
    assert "could not be read" in capsys.readouterr().err


# ---------------------------------------------------------------- real ACLs on a scratch tree

def _under_temp(p):
    tmp = os.path.normcase(str(Path(tempfile.gettempdir()).resolve()))
    return os.path.normcase(str(Path(p).resolve())).startswith(tmp + os.sep)


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    assert _under_temp(tmp_path)
    monkeypatch.setenv(rg.SANDBOX_ENV, str(tmp_path))
    root = tmp_path / "ref"
    (root / "libraries").mkdir(parents=True)
    (root / rg.SENTINEL).write_text("x")
    for name in ("a.xml", "b.xml", "c.xml"):
        (root / "libraries" / name).write_text("<x/>")
    own_or_skip(root, rg)                  # an elevated runner creates it owned by Administrators
    yield root
    subprocess.run(["icacls", str(root), "/reset", "/T", "/C", "/Q"], capture_output=True)


@win
def test_REAL_a_cut_child_the_sample_misses_fails_the_apply(scratch, capsys):
    cut = scratch / "libraries" / "c.xml"
    assert subprocess.run(["icacls", str(cut), "/inheritance:d", "/Q"], capture_output=True).returncode == 0
    assert rg._win_apply(scratch, yes=True) == 1
    assert rg._win_report(scratch, False)["state"] == "protected"     # the sample alone would have passed it
    assert "c.xml" in capsys.readouterr().err


@win
def test_REAL_apply_and_remove_round_trip_verifies_every_object(scratch, capsys):
    assert rg._win_apply(scratch, yes=True) == 0
    assert rg._win_report(scratch, True)["state"] == "protected"
    assert rg._win_remove(scratch, yes=True) == 0
    assert rg._win_report(scratch, True)["state"] == "absent"

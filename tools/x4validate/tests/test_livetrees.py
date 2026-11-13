"""One rule for where x4live journals may live (review r1-5, r1-6).

A journal must not overlap the PERSONAL TREE (the parent of the profile folder, which
holds every profile, its saves and uidata), the game install or the reference folder.
Before this, the persistence modules compared against the one profile folder only, and
`x4live automation --journal` compared against nothing.
"""
import copy
from pathlib import Path

import pytest

from x4validate import _livetrees as T
from x4validate import _paths
from x4validate._liveactions import ActionRefused
from x4validate._liveautomation import AutomationClient
from x4validate._livepersistence import PersistenceUnavailable, SlotVerifier


def test_overlap_rule():
    assert T.overlaps(Path("C:/a/b"), Path("C:/a"))
    assert T.overlaps(Path("C:/a"), Path("C:/a/b"))
    assert T.overlaps(Path("C:/a"), Path("C:/a"))
    assert not T.overlaps(Path("C:/a/b"), Path("C:/a/c"))
    assert T.personal_tree(Path("C:/Users/tester/Documents/Egosoft/X4/12345678")) == \
        Path("C:/Users/tester/Documents/Egosoft/X4").resolve()


def _verifier(profile, journal):
    return SlotVerifier(profile, journal, identity=lambda: {}, catalogue=lambda: {},
                        profile_peer=lambda: {}, clock=lambda: 1.0)


def test_persistence_journal_beside_the_profile_in_the_personal_tree_refuses(tmp_path):
    personal = tmp_path / "X4"
    profile = personal / "12345678"
    (profile / "save").mkdir(parents=True)
    journal = personal / "journal"
    journal.mkdir()
    with pytest.raises(PersistenceUnavailable, match="overlap"):
        _verifier(profile, journal)


def test_persistence_journal_outside_the_personal_tree_is_accepted(tmp_path):
    # Twin: a journal next to, not inside, the personal tree is fine.
    profile = tmp_path / "X4" / "12345678"
    (profile / "save").mkdir(parents=True)
    journal = tmp_path / "journal"
    journal.mkdir()
    _verifier(profile, journal)


class _NoPipe:
    @property
    def _h(self):
        raise AssertionError("the pipe was used before the journal was checked")


@pytest.mark.parametrize("which", ["reference", "game", "personal", "image"])
def test_automation_journal_overlapping_a_protected_tree_refuses_before_the_pipe(tmp_path, monkeypatch, which):
    ref, game, prof = tmp_path / "ref", tmp_path / "game", tmp_path / "X4" / "12345678"
    for folder in (ref, game, prof):
        folder.mkdir(parents=True)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    monkeypatch.setattr(_paths, "game_root", lambda: game)
    monkeypatch.setattr(_paths, "profile", lambda: prof)
    image = tmp_path / "other" / "X4.exe"
    journal = {"reference": ref / "j", "game": game / "j", "personal": prof.parent / "j",
               "image": image.parent / "j"}[which]
    journal.mkdir(parents=True)
    with pytest.raises(ActionRefused, match="overlap"):
        AutomationClient.connect(_NoPipe(), journal, image=str(image), profile_id="12345678",
                                 build="f" * 64, revision="a" * 64, mode="fresh")


def test_automation_journal_elsewhere_reaches_the_pipe(tmp_path, monkeypatch):
    # Twin: a journal outside every protected tree passes the check and reaches the pipe.
    ref, game, prof = tmp_path / "ref", tmp_path / "game", tmp_path / "X4" / "12345678"
    for folder in (ref, game, prof):
        folder.mkdir(parents=True)
    monkeypatch.setattr(_paths, "reference", lambda: ref)
    monkeypatch.setattr(_paths, "game_root", lambda: game)
    monkeypatch.setattr(_paths, "profile", lambda: prof)
    journal = tmp_path / "journals"
    journal.mkdir()
    with pytest.raises(AssertionError, match="pipe was used"):
        AutomationClient.connect(_NoPipe(), journal, image=str(tmp_path / "other" / "X4.exe"),
                                 profile_id="12345678", build="f" * 64, revision="a" * 64, mode="fresh")
